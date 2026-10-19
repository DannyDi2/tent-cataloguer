#!/usr/bin/env python3
"""dcat_core - core of the offline disk catalog for Linux.

Database: a single SQLite file (.dcat). With no disks mounted, everything
(structure, sizes, search) comes from this file.

CLI:
  dcat_core.py DB scan  PATH [--name NAME] [--folders-only]
  dcat_core.py DB update DISK_NAME [PATH]
  dcat_core.py DB search TEXT [--mode normal|all|any] [--whole] [--case]
  dcat_core.py DB list
  dcat_core.py DB export-csv FILE.csv
"""
import csv
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time

KIND_FILE, KIND_DIR, KIND_DISK, KIND_GROUP = 0, 1, 2, 3
REMOTE_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "davfs", "fuse.rclone"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  id INTEGER PRIMARY KEY,
  parent_id INTEGER,
  kind INTEGER NOT NULL,
  disk_id INTEGER,
  name TEXT NOT NULL,
  name_lc TEXT NOT NULL,
  relpath TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  mtime REAL,
  description TEXT NOT NULL DEFAULT '',
  desc_lc TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS ix_disk ON nodes(disk_id);
CREATE TABLE IF NOT EXISTS disks(
  node_id INTEGER PRIMARY KEY,
  source_path TEXT, volume TEXT, uuid TEXT, fstype TEXT,
  total INTEGER, free INTEGER, scanned REAL, remote INTEGER DEFAULT 0,
  folders_only INTEGER DEFAULT 0, ignore_json TEXT NOT NULL DEFAULT '[]'
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  name_lc, content='nodes', content_rowid='id', tokenize='trigram'
);
"""


class Cancelled(Exception):
    pass


def _migrate(db):
    """Make sure newer columns/indexes exist in databases created by older versions."""
    db.executescript(SCHEMA)
    try:
        db.execute("ALTER TABLE disks ADD COLUMN ignore_json TEXT NOT NULL DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass  # already exists
    return db


def open_db(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return _migrate(db)


# ------------------------------------------------ in-memory model (Save/Undo)
def new_memory_db():
    """New, empty, in-memory database."""
    return _migrate(sqlite3.connect(":memory:", isolation_level=None))


def load_into_memory(path):
    """Load a .dcat file into an in-memory connection (fast, and it keeps the
    file on disk untouched until an explicit Save)."""
    if not os.path.exists(path):
        return new_memory_db()
    disk = sqlite3.connect(path)
    mem = sqlite3.connect(":memory:", isolation_level=None)
    disk.backup(mem)
    disk.close()
    return _migrate(mem)  # ensure fts5/indexes/columns if the file is old


def save_to_file(db, path):
    """Write the current (in-memory) state to the given .dcat file."""
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    out = sqlite3.connect(tmp)
    db.backup(out)
    out.close()
    os.replace(tmp, path)


def snapshot(db):
    """Bytes of the current database state (for Undo/Redo)."""
    return db.serialize()


def restore(db, data):
    """Restore the state from bytes obtained with snapshot()."""
    db.deserialize(data)


# ---------------------------------------------------------------- formatting
def fmt_size(n):
    if n is None:
        return ""
    if n < 1024:
        return f"{n} Bytes"
    v = float(n)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        v /= 1024.0
        if v < 1024.0 or unit == "PB":
            if v < 10:
                return f"{v:.2f} {unit}"
            if v < 100:
                return f"{v:.1f} {unit}"
            return f"{v:.0f} {unit}"


def fmt_date(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ------------------------------------------------------------- disk info
def disk_info(path):
    d = {"volume": "", "uuid": "", "fstype": "", "total": 0, "free": 0, "remote": 0}
    try:
        u = shutil.disk_usage(path)
        d["total"], d["free"] = u.total, u.free
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["findmnt", "-J", "-o", "SOURCE,FSTYPE,UUID,LABEL", "-T", path],
            capture_output=True, text=True, timeout=10,
        ).stdout
        fs = json.loads(out)["filesystems"][0]
        d["fstype"] = fs.get("fstype") or ""
        d["uuid"] = fs.get("uuid") or ""
        d["volume"] = fs.get("label") or ""
        d["remote"] = 1 if d["fstype"] in REMOTE_FS else 0
    except Exception:
        pass
    return d


def mounted_places():
    """Useful mounted places: (mount_point, description)."""
    res = []
    try:
        with open("/proc/mounts") as f:
            lines = f.read().splitlines()
    except OSError:
        return res
    for ln in lines:
        p = ln.split()
        if len(p) < 3:
            continue
        mp = p[1].replace("\\040", " ")
        fst = p[2]
        if mp in ("/", "/home") or mp.startswith(("/media/", "/mnt/", "/run/media/")):
            info = disk_info(mp)
            label = info["volume"] or os.path.basename(mp) or "/"
            res.append((mp, f"{label}  [{fst}, {fmt_size(info['total'])}]"))
    return res


# ------------------------------------------------------ file manager integration
def node_real_path(db, node_id):
    """Filesystem path a catalogued node had when its disk was scanned
    (disk source path + relative path). None for catalog folders."""
    r = db.execute("SELECT n.kind, n.relpath, s.source_path FROM nodes n "
                   "LEFT JOIN disks s ON s.node_id = n.disk_id WHERE n.id=?",
                   (node_id,)).fetchone()
    if not r or r[0] == KIND_GROUP or not r[2]:
        return None
    _kind, rel, src = r
    return os.path.join(src, rel) if rel else src


def _run_ok(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def reveal_in_file_manager(path):
    """Open the file manager (Nemo on Linux Mint) showing `path`, with the item
    selected when the file manager supports it. Returns (ok, message); never raises."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return False, ("Path not found. Is the disk connected and mounted?\n"
                       "Expected at:\n" + path)
    uri = pathlib.Path(path).as_uri()
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    # 1) freedesktop.org FileManager1.ShowItems: opens the parent folder with the item selected
    if shutil.which("gdbus") and _run_ok([
            "gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
            "--object-path", "/org/freedesktop/FileManager1",
            "--method", "org.freedesktop.FileManager1.ShowItems", "['%s']" % uri, ""]):
        return True, "ok"
    # 2) fallback: open the containing folder (no selection)
    for fm in ("nemo", "xdg-open"):
        exe = shutil.which(fm)
        if exe:
            try:
                subprocess.Popen([exe, folder], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True, "ok"
            except OSError:
                continue
    return False, "No file manager found (tried gdbus, nemo and xdg-open)."


# -------------------------------------------------------------------- scanner
def _next_id(db):
    return db.execute("SELECT COALESCE(MAX(id),0) FROM nodes").fetchone()[0]


def fts_remove_disk(db, disk_id):
    db.execute(
        "INSERT INTO fts(fts,rowid,name_lc) SELECT 'delete',id,name_lc FROM nodes WHERE disk_id=?",
        (disk_id,),
    )


def delete_disk(db, disk_node_id):
    db.execute("BEGIN")
    fts_remove_disk(db, disk_node_id)
    db.execute("DELETE FROM nodes WHERE disk_id=?", (disk_node_id,))
    db.execute("DELETE FROM disks WHERE node_id=?", (disk_node_id,))
    db.execute("COMMIT")


def delete_group(db, group_id):
    ids = [r[0] for r in db.execute(
        """WITH RECURSIVE t(id,kind) AS (
             SELECT id,kind FROM nodes WHERE id=?
             UNION ALL SELECT n.id,n.kind FROM nodes n JOIN t ON n.parent_id=t.id
                       WHERE t.kind=? )
           SELECT id FROM t WHERE kind=?""", (group_id, KIND_GROUP, KIND_DISK))]
    for d in ids:
        delete_disk(db, d)
    sub = [r[0] for r in db.execute(
        """WITH RECURSIVE t(id) AS (SELECT id FROM nodes WHERE id=?
             UNION ALL SELECT n.id FROM nodes n JOIN t ON n.parent_id=t.id
             WHERE n.kind=?) SELECT id FROM t""", (group_id, KIND_GROUP))]
    db.execute("BEGIN")
    db.executemany("DELETE FROM nodes WHERE id=?", [(i,) for i in sub])
    db.execute("COMMIT")


def get_ignored(db, disk_id):
    """List of relative paths (inside the disk) to skip in future scans/updates."""
    r = db.execute("SELECT ignore_json FROM disks WHERE node_id=?", (disk_id,)).fetchone()
    if not r or not r[0]:
        return []
    try:
        return json.loads(r[0])
    except (TypeError, ValueError):
        return []


def set_ignored(db, disk_id, relpaths):
    db.execute("UPDATE disks SET ignore_json=? WHERE node_id=?",
              (json.dumps(sorted(set(relpaths))), disk_id))


def add_ignored(db, disk_id, relpath):
    cur = set(get_ignored(db, disk_id))
    cur.add(relpath)
    set_ignored(db, disk_id, cur)


def fts_remove_ids(db, ids):
    if not ids:
        return
    rows = db.execute(
        "SELECT id,name_lc FROM nodes WHERE id IN (%s)" % ",".join("?" * len(ids)), ids).fetchall()
    db.executemany("INSERT INTO fts(fts,rowid,name_lc) VALUES('delete',?,?)", rows)


def delete_and_ignore(db, node_id):
    """Delete a folder/file from INSIDE an already catalogued disk and remember
    its path, so that a future 'Update Disk' skips it again."""
    row = db.execute("SELECT parent_id,kind,disk_id,relpath,size FROM nodes WHERE id=?",
                     (node_id,)).fetchone()
    if not row:
        return
    parent_id, kind, disk_id, relpath, size = row
    db.execute("BEGIN")
    try:
        ids = [r[0] for r in db.execute(
            """WITH RECURSIVE t(id) AS (SELECT id FROM nodes WHERE id=?
                 UNION ALL SELECT n.id FROM nodes n JOIN t ON n.parent_id=t.id)
               SELECT id FROM t""", (node_id,))]
        fts_remove_ids(db, ids)
        db.executemany("DELETE FROM nodes WHERE id=?", [(i,) for i in ids])
        anc = parent_id
        while anc is not None:
            r2 = db.execute("SELECT parent_id FROM nodes WHERE id=?", (anc,)).fetchone()
            db.execute("UPDATE nodes SET size = size - ? WHERE id=?", (size, anc))
            if not r2:
                break
            anc = r2[0]
        if disk_id is not None:
            cur = set(get_ignored(db, disk_id))
            cur.add(relpath)
            set_ignored(db, disk_id, cur)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def make_group(db, name, parent_id):
    nid = _next_id(db) + 1
    db.execute(
        "INSERT INTO nodes(id,parent_id,kind,disk_id,name,name_lc) VALUES(?,?,?,?,?,?)",
        (nid, parent_id, KIND_GROUP, None, name, name.lower()))
    return nid


def scan_disk(db, root_path, name, parent_id=None, update_node=None,
              folders_only=False, progress=None, cancel=None, errors=None):
    """Catalog root_path. If update_node is given, that disk is rebuilt
    (item descriptions are kept). Returns the id of the disk node.

    Unreadable items never abort the scan. Each one is appended to `errors`
    (if a list is given) as (path, message, kept_old). When kept_old is True,
    the content catalogued earlier for that item was kept instead of dropped.
    Only an unreadable root folder raises (and then nothing is changed)."""
    root_path = os.path.abspath(root_path)
    if not os.path.isdir(root_path):
        raise FileNotFoundError(root_path)
    if errors is None:
        errors = []
    info = disk_info(root_path)
    descs = {}
    old_by_rel, old_children = {}, {}
    db.execute("BEGIN")
    try:
        if update_node:
            disk_id = update_node
            for row in db.execute(
                    "SELECT kind,name,relpath,size,mtime,description FROM nodes "
                    "WHERE disk_id=? AND id!=?", (disk_id, disk_id)):
                rel = row[2] or ""
                old_by_rel[rel] = row
                parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
                old_children.setdefault(parent_rel, []).append(row)
                if row[5]:
                    descs[rel] = row[5]
            fts_remove_disk(db, disk_id)
            db.execute("DELETE FROM nodes WHERE disk_id=? AND id!=?", (disk_id, disk_id))
        else:
            disk_id = _next_id(db) + 1
            db.execute(
                "INSERT INTO nodes(id,parent_id,kind,disk_id,name,name_lc) VALUES(?,?,?,?,?,?)",
                (disk_id, parent_id, KIND_DISK, disk_id, name, name.lower()))
        ignore_set = set(get_ignored(db, disk_id))  # deleted-and-remembered folders/files
        counter = [max(_next_id(db), disk_id)]
        rows, seen = [], [0]
        SQL = ("INSERT INTO nodes(id,parent_id,kind,disk_id,name,name_lc,relpath,size,mtime,"
               "description,desc_lc) VALUES(?,?,?,?,?,?,?,?,?,?,?)")

        def graft(row, parent_node):
            """Re-insert a previously catalogued item (and its subtree)."""
            kind, nm, rel, size, mtime, desc = row
            sz = size or 0
            if kind == KIND_FILE and folders_only:
                return sz
            counter[0] += 1
            nid = counter[0]
            if kind == KIND_DIR:
                for ch in old_children.get(rel or "", ()):
                    graft(ch, nid)
            d = desc or ""
            rows.append((nid, parent_node, kind, disk_id, nm, nm.lower(), rel, sz, mtime,
                         d, d.lower()))
            return sz

        def fail(path, rel, node_id, ex):
            if not rel:  # the root itself cannot be read: nothing to update
                raise ex
            errors.append((path, ex.strerror or str(ex), rel in old_by_rel))
            for ch in old_children.get(rel, ()):
                graft(ch, node_id)
            # keep the folder's previously recorded size (in "folders only" catalogs
            # the files are not stored, so the children alone would not add up)
            return (old_by_rel[rel][3] or 0) if rel in old_by_rel else 0

        def walk(path, rel, node_id):
            total = 0
            try:
                with os.scandir(path) as it:
                    entries = list(it)  # a read error here leaves nothing half-done
            except OSError as ex:
                return fail(path, rel, node_id, ex)
            for e in entries:
                seen[0] += 1
                if seen[0] % 1000 == 0:
                    if cancel and cancel():
                        raise Cancelled()
                    if progress:
                        progress(seen[0], e.path)
                nm = e.name
                r = rel + "/" + nm if rel else nm
                if r in ignore_set:
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                    isdir = e.is_dir(follow_symlinks=False)
                except OSError as ex:
                    errors.append((e.path, ex.strerror or str(ex), r in old_by_rel))
                    if r in old_by_rel:
                        total += graft(old_by_rel[r], node_id)
                    continue
                d = descs.get(r, "")
                if isdir:
                    counter[0] += 1
                    nid = counter[0]
                    sz = walk(e.path, r, nid)
                    rows.append((nid, node_id, KIND_DIR, disk_id, nm, nm.lower(), r, sz,
                                 st.st_mtime, d, d.lower()))
                    total += sz
                else:
                    total += st.st_size
                    if not folders_only:
                        counter[0] += 1
                        rows.append((counter[0], node_id, KIND_FILE, disk_id, nm, nm.lower(),
                                     r, st.st_size, st.st_mtime, d, d.lower()))
                if len(rows) >= 20000:
                    db.executemany(SQL, rows)
                    rows.clear()
            return total

        total = walk(root_path, "", disk_id)
        if rows:
            db.executemany(SQL, rows)
        try:
            root_mtime = os.stat(root_path).st_mtime
        except OSError as ex:
            errors.append((root_path, ex.strerror or str(ex), False))
            root_mtime = 0
        db.execute("UPDATE nodes SET size=?, mtime=? WHERE id=?",
                   (total, root_mtime, disk_id))
        db.execute("INSERT OR REPLACE INTO disks(node_id,source_path,volume,uuid,fstype,total,"
                   "free,scanned,remote,folders_only,ignore_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (disk_id, root_path, info["volume"], info["uuid"], info["fstype"],
                    info["total"], info["free"], time.time(), info["remote"],
                    1 if folders_only else 0, json.dumps(sorted(ignore_set))))
        db.execute("INSERT INTO fts(rowid,name_lc) SELECT id,name_lc FROM nodes WHERE disk_id=?",
                   (disk_id,))
        db.execute("COMMIT")
        return disk_id
    except BaseException:
        db.execute("ROLLBACK")
        raise


# -------------------------------------------------------------------- search
WILD = set("*?#[")


def _glob(term, whole, case):
    t = term if case else term.lower()
    t = t.replace("#", "[0-9]")
    return t if whole else f"*{t}*"


def _plain(term):
    return not (set(term) & WILD)


def scope_disk_ids(db, node_id):
    """Disks inside a node (group, or root when None)."""
    return [r[0] for r in db.execute(
        """WITH RECURSIVE t(id,kind) AS (
             SELECT id,kind FROM nodes WHERE parent_id IS ?
             UNION ALL SELECT n.id,n.kind FROM nodes n JOIN t ON n.parent_id=t.id WHERE t.kind=? )
           SELECT id FROM t WHERE kind=?""", (node_id, KIND_GROUP, KIND_DISK))]


def search(db, text="", mode="normal", whole=False, case=False, in_desc=True,
           kinds=(KIND_FILE, KIND_DIR, KIND_DISK), scope_node=None,
           size_op=None, size_val=0, date_from=None, date_to=None, limit=50000):
    """Return a list of tuples (id,kind,name,relpath,size,mtime,disk_name,description).

    mode: normal (whole text as one phrase) | all (every word) | any (some word).
    Wildcards: * ? # [abc]. scope_node: None = everything; group/disk/folder.
    """
    cond, par = [], []
    terms = [text] if mode == "normal" else text.split()
    terms = [t for t in terms if t != ""]
    tconds = []
    for t in terms:
        ncol, dcol = ("name", "description") if case else ("name_lc", "desc_lc")
        g = _glob(t, whole, case)
        if not case and not whole and _plain(t) and len(t) >= 3:
            c = "n.id IN (SELECT rowid FROM fts WHERE fts MATCH ?)"
            p = ['"' + t.lower().replace('"', '""') + '"']
        else:
            c, p = f"n.{ncol} GLOB ?", [g]
        if in_desc:
            c = f"({c} OR n.{dcol} GLOB ?)"
            p.append(g)
        tconds.append(c)
        par.extend(p)
    if tconds:
        cond.append("(" + (" OR " if mode == "any" else " AND ").join(tconds) + ")")
    cond.append("n.kind IN (%s)" % ",".join(str(int(k)) for k in kinds))
    if scope_node is not None:
        r = db.execute("SELECT kind,disk_id,relpath FROM nodes WHERE id=?", (scope_node,)).fetchone()
        if r:
            k, did, rel = r
            if k == KIND_GROUP:
                ids = scope_disk_ids(db, scope_node)
                cond.append("n.disk_id IN (%s)" % (",".join(map(str, ids)) or "NULL"))
            elif k == KIND_DISK:
                cond.append("n.disk_id=?")
                par.append(did)
            else:
                esc = rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                cond.append("n.disk_id=? AND n.relpath LIKE ? ESCAPE '\\'")
                par += [did, esc + "/%"]
    if size_op and size_op in (">=", "<=", "="):
        cond.append(f"n.size {size_op} ?")
        par.append(size_val)
    if date_from is not None:
        cond.append("n.mtime >= ?")
        par.append(date_from)
    if date_to is not None:
        cond.append("n.mtime <= ?")
        par.append(date_to)
    sql = ("SELECT n.id,n.kind,n.name,n.relpath,n.size,n.mtime,d.name,n.description "
           "FROM nodes n LEFT JOIN nodes d ON d.id=n.disk_id WHERE " + " AND ".join(cond) +
           " ORDER BY d.name_lc, n.relpath LIMIT ?")
    par.append(limit)
    return db.execute(sql, par).fetchall()


def path_of(db, node_id):
    """'Disk/folder/sub' path for a node."""
    r = db.execute("SELECT n.relpath,d.name FROM nodes n LEFT JOIN nodes d ON d.id=n.disk_id "
                   "WHERE n.id=?", (node_id,)).fetchone()
    if not r:
        return ""
    rel, dn = r
    return dn + ("/" + rel if rel else "") if dn else ""


def export_csv(db, out_path):
    n = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Disk", "Path", "Name", "Type", "Size", "Modified", "Description"])
        for dn, rel, nm, kind, sz, mt, ds in db.execute(
                "SELECT d.name,n.relpath,n.name,n.kind,n.size,n.mtime,n.description "
                "FROM nodes n JOIN nodes d ON d.id=n.disk_id WHERE n.kind IN (0,1) "
                "ORDER BY d.name_lc,n.relpath"):
            w.writerow([dn, rel, nm, "folder" if kind == KIND_DIR else "file", sz,
                        fmt_date(mt), ds])
            n += 1
    return n


# ------------------------------------------------------------------------ CLI
def _cli(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    db = open_db(argv[1])
    cmd, rest = argv[2], argv[3:]
    opt = lambda k, d=None: rest[rest.index(k) + 1] if k in rest else d  # noqa: E731
    if cmd == "scan":
        path = rest[0]
        name = opt("--name", os.path.basename(os.path.abspath(path)) or "root")
        t = time.time()
        errs = []
        did = scan_disk(db, path, name, folders_only="--folders-only" in rest, errors=errs,
                        progress=lambda n, p: n % 20000 == 0 and print(f"\r{n} items...", end="", file=sys.stderr))
        for ep, em, _k in errs:
            print(f"UNREADABLE: {ep}: {em}", file=sys.stderr)
        sz, = db.execute("SELECT size FROM nodes WHERE id=?", (did,)).fetchone()
        cnt, = db.execute("SELECT COUNT(*) FROM nodes WHERE disk_id=?", (did,)).fetchone()
        print(f"\nOK: '{name}' {cnt} items, {fmt_size(sz)}, {time.time()-t:.1f}s")
    elif cmd == "update":
        r = db.execute("SELECT n.id,s.source_path,s.folders_only FROM nodes n JOIN disks s "
                       "ON s.node_id=n.id WHERE n.name=?", (rest[0],)).fetchone()
        if not r:
            print("Disk not found")
            return 1
        errs = []
        scan_disk(db, rest[1] if len(rest) > 1 else r[1], rest[0], update_node=r[0],
                  folders_only=bool(r[2]), errors=errs)
        for ep, em, k in errs:
            print(f"UNREADABLE: {ep}: {em}" + (" (previous data kept)" if k else ""), file=sys.stderr)
        print("Updated" + (f" with {len(errs)} unreadable item(s)" if errs else ""))
    elif cmd == "search":
        t = time.time()
        res = search(db, rest[0], mode=opt("--mode", "normal"), whole="--whole" in rest,
                     case="--case" in rest)
        for _id, k, nm, rel, sz, mt, dn, ds in res[:200]:
            print(f"{dn}/{rel}\t{fmt_size(sz)}\t{fmt_date(mt)}")
        print(f"-- {len(res)} results in {time.time()-t:.3f}s", file=sys.stderr)
    elif cmd == "list":
        for nm, sz, tot, free, sc, src in db.execute(
                "SELECT n.name,n.size,s.total,s.free,s.scanned,s.source_path FROM nodes n "
                "JOIN disks s ON s.node_id=n.id ORDER BY n.name_lc"):
            print(f"{nm}\t{fmt_size(sz)}\t{fmt_size(tot)}\t{fmt_date(sc)}\t{src}")
    elif cmd == "export-csv":
        print(export_csv(db, rest[0]), "rows")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
