#!/usr/bin/env python3
"""dcat_core - core of the offline disk catalog for Linux.

Database: a single SQLite file (.dcat). With no disks mounted, everything
(structure, sizes, search) comes from this file.

CLI:
  dcat_core.py DB scan  PATH [--name NAME] [--folders-only] [--archives] [--media]
  dcat_core.py DB update DISK_NAME [PATH]
  dcat_core.py DB search TEXT [--mode normal|all|any] [--whole] [--case] [--type CATEGORY]
  dcat_core.py DB list
  dcat_core.py DB stats [DISK_NAME]
  dcat_core.py DB dups [MIN_MB]
  dcat_core.py DB export-csv FILE.csv
"""
import csv
import glob
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import zipfile

KIND_FILE, KIND_DIR, KIND_DISK, KIND_GROUP, KIND_ARCHIVE = 0, 1, 2, 3, 4
FILE_KINDS = (KIND_FILE, KIND_ARCHIVE)        # things that are files on disk
FOLDER_KINDS = (KIND_DIR, KIND_DISK, KIND_GROUP, KIND_ARCHIVE)  # things you can open
ARC_SEP = "::"  # relpath of an item inside an archive: "dir/a.zip::inner/file.txt"
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

# columns added by later versions: (table, column definition)
_NEW_COLUMNS = (
    ("disks", "ignore_json TEXT NOT NULL DEFAULT '[]'"),
    ("nodes", "meta TEXT NOT NULL DEFAULT ''"),          # media info (tags, EXIF, duration)
    ("disks", "color TEXT NOT NULL DEFAULT ''"),          # background color in search results
    ("disks", "mount TEXT NOT NULL DEFAULT ''"),          # mount point when it was scanned
    ("disks", "archives INTEGER NOT NULL DEFAULT 0"),     # archive contents were indexed
    ("disks", "media INTEGER NOT NULL DEFAULT 0"),        # media info was read
)
# small partial indexes: only the few rows that have a description / media info,
# so searching those columns never has to read the whole catalog
_NEW_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_desc ON nodes(id) WHERE desc_lc<>''",
    "CREATE INDEX IF NOT EXISTS ix_meta ON nodes(id) WHERE meta<>''",
)

# file categories for the "Type" filter and the statistics
CATEGORIES = {
    "Video": "mkv mp4 m4v avi mov wmv flv webm mpg mpeg ts m2ts vob 3gp ogv divx rmvb",
    "Audio": "mp3 flac ogg opus m4a aac wma wav ape wv aiff aif alac mka mid midi",
    "Images": "jpg jpeg png gif bmp webp tif tiff heic heif raw cr2 cr3 nef arw dng orf rw2 svg psd xcf ico",
    "Documents": "pdf doc docx odt rtf txt md xls xlsx ods csv ppt pptx odp pages numbers key tex log",
    "E-books": "epub mobi azw azw3 kfx fb2 djvu cbz cbr lit prc",
    "Archives": "zip rar 7z tar gz tgz bz2 tbz2 xz txz zst iso img dmg cab arj lzh",
    "Programs": "exe msi deb rpm appimage apk jar sh bat cmd flatpakref snap",
    "Code": "py js ts html htm css c h cpp hpp java cs go rs rb php pl lua json xml yml yaml toml ini sql ipynb",
    "Subtitles": "srt sub ass ssa vtt idx",
}
EXT_CATEGORY = {e: cat for cat, exts in CATEGORIES.items() for e in exts.split()}

# archives whose contents can be listed (double extensions handled in archive_kind)
ARCHIVE_EXTS = {"zip", "rar", "7z", "tar", "tgz", "tbz", "tbz2", "txz", "iso", "cab", "arj", "lzh"}
TAR_COMPRESSED_MAX = 512 * 1024 ** 2   # compressed tars must be read whole: skip bigger ones
ARCHIVE_MAX_ENTRIES = 200000
AUDIO_EXTS = set("mp3 flac ogg opus m4a aac wma wav ape wv aiff aif mka".split())
IMAGE_EXTS = set("jpg jpeg tif tiff png webp heic".split())
VIDEO_EXTS = set("mkv mp4 m4v avi mov wmv flv webm mpg mpeg ts m2ts 3gp ogv".split())

# pleasant, clearly distinct pastel colors for disks (light theme); a darker
# version of each is used automatically in the dark theme
DISK_PALETTE = [
    "#cfe5ff", "#d4f2d9", "#ffe3c2", "#f3d6f7", "#fff3b0", "#c9f0ee",
    "#ffd6d6", "#e2dcff", "#dff0c4", "#ffdcef", "#d0e7f2", "#f2e3cf",
]


class Cancelled(Exception):
    pass


def _migrate(db):
    """Make sure newer columns/indexes exist in databases created by older versions."""
    db.executescript(SCHEMA)
    for table, coldef in _NEW_COLUMNS:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
        except sqlite3.OperationalError:
            pass  # already exists
    for sql in _NEW_INDEXES:
        db.execute(sql)
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


def fmt_duration(sec):
    sec = int(round(sec or 0))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def category_of(name):
    return EXT_CATEGORY.get(ext_of(name), "Other")


def display_rel(rel):
    """Relative path as shown to people (archive separator becomes '/')."""
    return rel.replace(ARC_SEP, "/")


def parent_rel(rel):
    """Relative path of the parent of `rel` (archive-aware)."""
    if ARC_SEP in rel:
        base, inner = rel.split(ARC_SEP, 1)
        return base + ARC_SEP + inner.rsplit("/", 1)[0] if "/" in inner else base
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


# ------------------------------------------------------------- disk info
def disk_info(path):
    d = {"volume": "", "uuid": "", "fstype": "", "total": 0, "free": 0, "remote": 0, "mount": ""}
    try:
        u = shutil.disk_usage(path)
        d["total"], d["free"] = u.total, u.free
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["findmnt", "-J", "-o", "SOURCE,TARGET,FSTYPE,UUID,LABEL", "-T", path],
            capture_output=True, text=True, timeout=10,
        ).stdout
        fs = json.loads(out)["filesystems"][0]
        d["fstype"] = fs.get("fstype") or ""
        d["uuid"] = fs.get("uuid") or ""
        d["volume"] = fs.get("label") or ""
        d["mount"] = fs.get("target") or ""
        d["remote"] = 1 if d["fstype"] in REMOTE_FS else 0
    except Exception:
        pass
    return d


def _interesting_mount(mp):
    return mp in ("/", "/home") or mp.startswith(("/media/", "/mnt/", "/run/media/"))


def mount_points():
    """Set of currently mounted 'interesting' mount points (cheap: reads /proc/mounts)."""
    res = set()
    try:
        with open("/proc/mounts") as f:
            for ln in f:
                p = ln.split()
                if len(p) >= 3:
                    mp = p[1].replace("\\040", " ")
                    if _interesting_mount(mp):
                        res.add(mp)
    except OSError:
        pass
    return res


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
        if _interesting_mount(mp):
            info = disk_info(mp)
            label = info["volume"] or os.path.basename(mp) or "/"
            res.append((mp, f"{label}  [{fst}, {fmt_size(info['total'])}]"))
    return res


def catalogued_disks_on_mount(db, mount):
    """Catalogued disks that live on the filesystem now mounted at `mount`.
    Returns [(disk_id, name, path_now)], matched by filesystem UUID."""
    info = disk_info(mount)
    if not info["uuid"]:
        return []
    res = []
    for did, name, src, old_mount in db.execute(
            "SELECT s.node_id, n.name, s.source_path, s.mount FROM disks s JOIN nodes n "
            "ON n.id=s.node_id WHERE s.uuid=? ORDER BY n.name_lc", (info["uuid"],)):
        src = src or ""
        if old_mount and (src == old_mount or src.startswith(old_mount.rstrip("/") + "/")):
            rel = os.path.relpath(src, old_mount)
            now = mount if rel == "." else os.path.join(mount, rel)
        elif os.path.isdir(src):
            now = src
        else:
            now = mount
        res.append((did, name, now))
    return res


# ------------------------------------------------------------ disk colors
def disk_colors(db):
    """{disk_id: '#rrggbb'} for every disk; disks without a chosen color get
    one from the palette (stable: based on their order of creation)."""
    res = {}
    for i, (did, color) in enumerate(db.execute(
            "SELECT node_id, color FROM disks ORDER BY node_id")):
        res[did] = color or DISK_PALETTE[i % len(DISK_PALETTE)]
    return res


def set_disk_color(db, disk_id, color):
    db.execute("UPDATE disks SET color=? WHERE node_id=?", (color or "", disk_id))


# ------------------------------------------------------ file manager integration
def node_real_path(db, node_id):
    """Filesystem path a catalogued node had when its disk was scanned
    (disk source path + relative path). None for catalog folders. For an item
    inside an archive, the archive itself."""
    r = db.execute("SELECT n.kind, n.relpath, s.source_path FROM nodes n "
                   "LEFT JOIN disks s ON s.node_id = n.disk_id WHERE n.id=?",
                   (node_id,)).fetchone()
    if not r or r[0] == KIND_GROUP or not r[2]:
        return None
    _kind, rel, src = r
    rel = rel.split(ARC_SEP, 1)[0]
    return os.path.join(src, rel) if rel else src


def resolve_real_path(db, node_id):
    """Where the item is NOW: its recorded path if it exists, otherwise the same
    place on its disk if that disk is mounted elsewhere (matched by UUID).
    Returns (path or None, disk_name)."""
    path = node_real_path(db, node_id)
    r = db.execute("SELECT d.name, s.uuid, s.source_path, n.relpath FROM nodes n "
                   "JOIN nodes d ON d.id=n.disk_id JOIN disks s ON s.node_id=n.disk_id "
                   "WHERE n.id=?", (node_id,)).fetchone()
    if not r:
        return (path if path and os.path.exists(path) else None), ""
    disk_name, uuid, src, rel = r
    if path and os.path.exists(path):
        return path, disk_name
    if uuid:
        for mp in sorted(mount_points()):
            for did, _nm, now in catalogued_disks_on_mount(db, mp):
                if did == db.execute("SELECT disk_id FROM nodes WHERE id=?", (node_id,)).fetchone()[0]:
                    base = rel.split(ARC_SEP, 1)[0]
                    cand = os.path.join(now, base) if base else now
                    if os.path.exists(cand):
                        return cand, disk_name
    return None, disk_name


def open_with_default_app(path):
    """Open/play a file with the desktop's default application. Returns (ok, message)."""
    for cmd in (["xdg-open", path], ["gio", "open", path]):
        exe = shutil.which(cmd[0])
        if exe:
            try:
                subprocess.Popen([exe] + cmd[1:], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True, "ok"
            except OSError:
                continue
    return False, "No program found to open files (tried xdg-open and gio)."


def _desktop_file_path(desktop_id):
    """Full path of an application's .desktop file (user, system and flatpak locations)."""
    dirs = [os.path.expanduser("~/.local/share")] + \
        (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") + \
        ["/var/lib/flatpak/exports/share", os.path.expanduser("~/.local/share/flatpak/exports/share")]
    for d in dirs:
        cand = os.path.join(d, "applications", desktop_id)
        if os.path.isfile(cand):
            return cand
    return None


def _default_app_for(path, cache):
    """.desktop file of the default application for this file (cached per extension)."""
    key = ext_of(os.path.basename(path)) or os.path.basename(path)
    if key in cache:
        return cache[key]
    app = None
    try:
        mime = subprocess.run(["xdg-mime", "query", "filetype", path], capture_output=True,
                              text=True, timeout=10).stdout.strip()
        if mime:
            did = subprocess.run(["xdg-mime", "query", "default", mime], capture_output=True,
                                 text=True, timeout=10).stdout.strip().splitlines()
            app = _desktop_file_path(did[0]) if did else None
    except (OSError, subprocess.TimeoutExpired):
        app = None
    cache[key] = app
    return app


def open_many_with_default_apps(paths):
    """Open several files at once. Files that share a default application are
    passed to it in ONE launch (songs become a playlist in the music player;
    PDFs open together in the PDF reader). Returns (ok, message)."""
    if len(paths) == 1:
        return open_with_default_app(paths[0])
    gio = shutil.which("gio")
    groups, order, cache = {}, [], {}
    for p in paths:
        app = _default_app_for(p, cache) if gio and shutil.which("xdg-mime") else None
        if app not in groups:
            groups[app] = []
            order.append(app)
        groups[app].append(p)
    failed = []
    for app in order:
        files = groups[app]
        if app:
            try:
                subprocess.Popen([gio, "launch", app] + files, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                continue
            except OSError:
                pass
        for f in files:  # no known default app: one by one
            ok, _m = open_with_default_app(f)
            if not ok:
                failed.append(f)
    if failed:
        return False, f"{len(failed)} file(s) could not be opened."
    return True, "ok"


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


# ------------------------------------------------------------------ archives
_SEVENZIP = []  # cached path of a 7-Zip executable ("" = none found)


def sevenzip_exe():
    """A 7-Zip command line program: system 7zz/7z/7za, or the one bundled with PeaZip."""
    if not _SEVENZIP:
        found = ""
        for name in ("7zz", "7z", "7za"):
            found = shutil.which(name) or ""
            if found:
                break
        if not found:
            pats = ["/usr/lib/peazip/res/bin/7z/7z", "/usr/lib/peazip/res/7z/7z",
                    "/opt/peazip*/res/bin/7z/7z", "/opt/peazip*/res/7z/7z",
                    os.path.expanduser("~/.local/share/peazip*/res/bin/7z/7z"),
                    "/var/lib/flatpak/app/io.github.peazip.PeaZip/current/active/files/*/res/bin/7z/7z"]
            for pat in pats:
                hits = [h for h in glob.glob(pat) if os.access(h, os.X_OK)]
                if hits:
                    found = hits[0]
                    break
        _SEVENZIP.append(found)
    return _SEVENZIP[0]


def archive_kind(name, size):
    """'zip' | 'tar' | '7z' when the contents of this file can be listed, else None."""
    lc = name.lower()
    if lc.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz", ".tbz2", ".txz")):
        return "tar" if size <= TAR_COMPRESSED_MAX else None
    ext = ext_of(lc)
    if ext not in ARCHIVE_EXTS:
        return None
    if ext == "zip":
        return "zip"
    if ext == "tar":
        return "tar"
    return "7z" if sevenzip_exe() else None


def _list_zip(path):
    with zipfile.ZipFile(path) as z:
        for zi in z.infolist():
            try:
                mt = time.mktime(zi.date_time + (0, 0, -1))
            except (OverflowError, ValueError):
                mt = None
            yield zi.filename.rstrip("/"), zi.is_dir(), zi.file_size, mt


def _list_tar(path):
    with tarfile.open(path, "r:*") as t:
        for ti in t:
            yield ti.name.rstrip("/"), ti.isdir(), ti.size, ti.mtime


def _list_7z(path):
    exe = sevenzip_exe()
    out = subprocess.run([exe, "l", "-slt", "-pnopassword", "-sccUTF-8", path],
                         capture_output=True, text=True, timeout=180,
                         stdin=subprocess.DEVNULL, errors="replace")
    text = out.stdout
    if "----------" not in text:
        raise OSError(out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "cannot list")
    body = text.split("----------", 1)[1]
    for block in body.split("\n\n"):
        f = {}
        for ln in block.splitlines():
            if " = " in ln:
                k, v = ln.split(" = ", 1)
                f[k.strip()] = v
        p = f.get("Path")
        if not p:
            continue
        isdir = f.get("Folder") == "+" or f.get("Attributes", "").startswith("D")
        try:
            size = int(f.get("Size") or 0)
        except ValueError:
            size = 0
        mt = None
        if f.get("Modified"):
            try:
                mt = time.mktime(time.strptime(f["Modified"][:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                pass
        yield p.replace("\\", "/").rstrip("/"), isdir, size, mt
    if out.returncode not in (0, 1) and not body.strip():
        raise OSError("cannot list")


def list_archive(path, kind):
    """Entries of an archive as a list of (inner_path, is_dir, size, mtime)."""
    fn = {"zip": _list_zip, "tar": _list_tar, "7z": _list_7z}[kind]
    res = []
    for name, isdir, size, mt in fn(path):
        while name.startswith("./"):
            name = name[2:]
        if name and name != "." and not name.startswith("/") and ".." not in name.split("/"):
            res.append((name, isdir, size, mt))
            if len(res) >= ARCHIVE_MAX_ENTRIES:
                break
    return res


# ---------------------------------------------------------------- media info
_MEDIA_LIBS = {}


def _lib(name):
    if name not in _MEDIA_LIBS:
        try:
            if name == "mutagen":
                import mutagen
                _MEDIA_LIBS[name] = mutagen
            elif name == "PIL":
                from PIL import Image
                _MEDIA_LIBS[name] = Image
            elif name == "ffprobe":
                _MEDIA_LIBS[name] = shutil.which("ffprobe")
        except Exception:  # noqa: BLE001
            _MEDIA_LIBS[name] = None
    return _MEDIA_LIBS[name]


def media_support():
    """Which media readers are available: {'music': bool, 'photos': bool, 'videos': bool}."""
    return {"music": bool(_lib("mutagen")), "photos": bool(_lib("PIL")),
            "videos": bool(_lib("ffprobe"))}


def _audio_meta(path):
    mutagen = _lib("mutagen")
    if not mutagen:
        return ""
    f = mutagen.File(path, easy=True)
    if f is None:
        return ""
    tags = f.tags or {}

    def tag(k):
        v = tags.get(k) if hasattr(tags, "get") else None
        return str(v[0]).strip() if v else ""
    parts = [tag("artist"), tag("album"), tag("title"), tag("date")[:4]]
    length = getattr(getattr(f, "info", None), "length", 0)
    if length:
        parts.append(fmt_duration(length))
    return " · ".join(p for p in parts if p)


def _image_meta(path):
    Image = _lib("PIL")
    if not Image:
        return ""
    with Image.open(path) as im:
        parts = [f"{im.width}×{im.height}"]
        try:
            ex = im.getexif()
            sub = ex.get_ifd(0x8769) if ex else {}
            dt = (sub.get(36867) or ex.get(306) or "") if ex else ""
            if dt:
                parts.append(str(dt)[:16].replace(":", "-", 2))
            cam = " ".join(x for x in (str(ex.get(271) or "").strip(),
                                        str(ex.get(272) or "").strip()) if x) if ex else ""
            if cam:
                # "Canon Canon EOS 80D" -> "Canon EOS 80D"
                words = cam.split()
                if len(words) > 1 and words[1].lower().startswith(words[0].lower()):
                    cam = " ".join(words[1:])
                parts.append(cam)
        except Exception:  # noqa: BLE001
            pass
    return " · ".join(parts)


def _video_meta(path):
    exe = _lib("ffprobe")
    if not exe:
        return ""
    out = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", path],
        capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL).stdout
    j = json.loads(out or "{}")
    parts = []
    st = (j.get("streams") or [{}])[0]
    if st.get("width"):
        parts.append(f"{st['width']}×{st['height']}")
    dur = (j.get("format") or {}).get("duration")
    if dur:
        parts.append(fmt_duration(float(dur)))
    return " · ".join(parts)


def read_media_meta(path, name):
    """Short media description for a file, '' if not a media file or unreadable."""
    ext = ext_of(name)
    try:
        if ext in AUDIO_EXTS:
            return _audio_meta(path)
        if ext in IMAGE_EXTS:
            return _image_meta(path)
        if ext in VIDEO_EXTS:
            return _video_meta(path)
    except Exception:  # noqa: BLE001  (corrupt files never stop a scan)
        return ""
    return ""


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
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        rows = db.execute(
            "SELECT id,name_lc FROM nodes WHERE id IN (%s)" % ",".join("?" * len(chunk)),
            chunk).fetchall()
        db.executemany("INSERT INTO fts(fts,rowid,name_lc) VALUES('delete',?,?)", rows)


def delete_and_ignore(db, node_id):
    """Delete a folder/file from INSIDE an already catalogued disk and remember
    its path, so that a future 'Update Disk' skips it again."""
    row = db.execute("SELECT parent_id,kind,disk_id,relpath,size FROM nodes WHERE id=?",
                     (node_id,)).fetchone()
    if not row:
        return
    parent_id, kind, disk_id, relpath, size = row
    inside_archive = ARC_SEP in relpath
    db.execute("BEGIN")
    try:
        ids = [r[0] for r in db.execute(
            """WITH RECURSIVE t(id) AS (SELECT id FROM nodes WHERE id=?
                 UNION ALL SELECT n.id FROM nodes n JOIN t ON n.parent_id=t.id)
               SELECT id FROM t""", (node_id,))]
        fts_remove_ids(db, ids)
        db.executemany("DELETE FROM nodes WHERE id=?", [(i,) for i in ids])
        if not inside_archive:  # archive contents do not count in folder sizes
            anc = parent_id
            while anc is not None:
                r2 = db.execute("SELECT parent_id,kind FROM nodes WHERE id=?", (anc,)).fetchone()
                if r2 and r2[1] == KIND_GROUP:
                    break
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
              folders_only=False, progress=None, cancel=None, errors=None,
              archives=False, media=False):
    """Catalog root_path. If update_node is given, that disk is rebuilt
    (item descriptions are kept). Returns the id of the disk node.

    progress(items_seen, current_path, fraction) is called a few times per second;
    fraction is 0..1, or None when the total is unknown (first read of a folder).
    archives: also catalog what is inside zip/rar/7z/tar/iso files.
    media:    read music tags, photo EXIF and video duration (stored in 'meta').
    On an update, unchanged archives and media files (same size and date) reuse
    what was read before, so updates stay fast.

    Unreadable items never abort the scan. Each one is appended to `errors`
    (if a list is given) as (path, message, kept_old). When kept_old is True,
    the content catalogued earlier for that item was kept instead of dropped;
    kept_old is None for archives whose contents could not be listed (the
    archive itself is still catalogued).
    Only an unreadable root folder raises (and then nothing is changed)."""
    root_path = os.path.abspath(root_path)
    if not os.path.isdir(root_path):
        raise FileNotFoundError(root_path)
    if errors is None:
        errors = []
    if folders_only:
        archives = media = False
    info = disk_info(root_path)
    descs = {}
    old_by_rel, old_children = {}, {}
    old_media = False  # media info in the old catalog can be reused
    db.execute("BEGIN")
    try:
        if update_node:
            disk_id = update_node
            r0 = db.execute("SELECT media FROM disks WHERE node_id=?", (disk_id,)).fetchone()
            old_media = bool(r0 and r0[0])
            for row in db.execute(
                    "SELECT kind,name,relpath,size,mtime,description,meta FROM nodes "
                    "WHERE disk_id=? AND id!=?", (disk_id, disk_id)):
                rel = row[2] or ""
                old_by_rel[rel] = row
                old_children.setdefault(parent_rel(rel), []).append(row)
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
        last_tick = [time.monotonic()]
        SQL = ("INSERT INTO nodes(id,parent_id,kind,disk_id,name,name_lc,relpath,size,mtime,"
               "description,desc_lc,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")

        # what "100%" means: on an update, the number of items found last time;
        # on a first read of a whole disk, the space in use on it; otherwise unknown
        expected_items = sum(1 for r in old_by_rel if ARC_SEP not in r)
        expected_bytes = 0
        if not expected_items and info["total"] and (
                os.path.ismount(root_path) or root_path == info["mount"]):
            expected_bytes = max(info["total"] - info["free"], 1)
        bytes_done = [0]

        def fraction():
            if expected_items:
                return min(seen[0] / expected_items, 0.999)
            if expected_bytes:
                return min(bytes_done[0] / expected_bytes, 0.999)
            return None

        def tick(path, force=False):
            """Progress/cancel check: every 1000 items, or after slow work."""
            now = time.monotonic()
            if force or now - last_tick[0] > 0.25:
                last_tick[0] = now
                if cancel and cancel():
                    raise Cancelled()
                if progress:
                    progress(seen[0], path, fraction())

        def add(kind, parent, nm, rel, size, mtime, meta=""):
            counter[0] += 1
            d = descs.get(rel, "")
            rows.append((counter[0], parent, kind, disk_id, nm, nm.lower(), rel, size, mtime,
                         d, d.lower(), meta))
            if len(rows) >= 20000:
                db.executemany(SQL, rows)
                rows.clear()
            return counter[0]

        def graft(row, parent_node):
            """Re-insert a previously catalogued item (and its subtree)."""
            kind, nm, rel, size, mtime, desc, meta = row
            sz = size or 0
            if kind in FILE_KINDS and folders_only:
                return sz
            if kind == KIND_ARCHIVE and not archives:
                kind = KIND_FILE
            counter[0] += 1
            nid = counter[0]
            if kind in (KIND_DIR, KIND_ARCHIVE):
                for ch in old_children.get(rel or "", ()):
                    graft(ch, nid)
            d = desc or ""
            rows.append((nid, parent_node, kind, disk_id, nm, nm.lower(), rel, sz, mtime,
                         d, d.lower(), (meta or "") if media else ""))
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

        def unchanged(rel, kind, st):
            o = old_by_rel.get(rel)
            return (o is not None and o[0] == kind and o[3] == st.st_size
                    and o[4] is not None and abs(o[4] - st.st_mtime) < 1)

        def add_archive(path, rel, nm, st, akind):
            """Catalog an archive file and its contents."""
            if unchanged(rel, KIND_ARCHIVE, st) and old_children.get(rel):
                nid = add(KIND_ARCHIVE, current_parent[0], nm, rel, st.st_size, st.st_mtime)
                for ch in old_children.get(rel, ()):
                    graft(ch, nid)
                return True
            try:
                entries = list_archive(path, akind)
            except Exception as ex:  # noqa: BLE001
                msg = getattr(ex, "strerror", None) or str(ex) or type(ex).__name__
                errors.append((path, "archive contents not listed: " + msg, None))
                return False
            finally:
                tick(path, force=False)
            if not entries:
                return False
            nid = add(KIND_ARCHIVE, current_parent[0], nm, rel, st.st_size, st.st_mtime)
            folders = {"": nid}

            def folder(inner):
                if inner in folders:
                    return folders[inner]
                par = folder(inner.rsplit("/", 1)[0] if "/" in inner else "")
                fid = add(KIND_DIR, par, inner.rsplit("/", 1)[-1], rel + ARC_SEP + inner, 0, None)
                folders[inner] = fid
                return fid
            for inner, isdir, size, mt in sorted(entries, key=lambda e: e[0]):
                if isdir:
                    folder(inner)
                else:
                    par = folder(inner.rsplit("/", 1)[0] if "/" in inner else "")
                    add(KIND_FILE, par, inner.rsplit("/", 1)[-1], rel + ARC_SEP + inner,
                        size or 0, mt)
            return True

        current_parent = [None]

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
                    tick(e.path, force=True)
                else:
                    tick(e.path)  # cheap time check: keeps the progress moving on slow disks
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
                if isdir:
                    counter[0] += 1
                    nid = counter[0]
                    sz = walk(e.path, r, nid)
                    d = descs.get(r, "")
                    rows.append((nid, node_id, KIND_DIR, disk_id, nm, nm.lower(), r, sz,
                                 st.st_mtime, d, d.lower(), ""))
                    if len(rows) >= 20000:
                        db.executemany(SQL, rows)
                        rows.clear()
                    total += sz
                    continue
                total += st.st_size
                bytes_done[0] += st.st_size
                if folders_only:
                    continue
                if archives and e.is_file(follow_symlinks=False):
                    akind = archive_kind(nm, st.st_size)
                    if akind:
                        current_parent[0] = node_id
                        if add_archive(e.path, r, nm, st, akind):
                            continue
                meta = ""
                if media and e.is_file(follow_symlinks=False):
                    if old_media and unchanged(r, KIND_FILE, st):
                        meta = old_by_rel[r][6] or ""
                    else:
                        ext = ext_of(nm)
                        if ext in AUDIO_EXTS or ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                            meta = read_media_meta(e.path, nm)
                            tick(e.path)
                add(KIND_FILE, node_id, nm, r, st.st_size, st.st_mtime, meta)
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
        old = db.execute("SELECT color FROM disks WHERE node_id=?", (disk_id,)).fetchone()
        db.execute("INSERT OR REPLACE INTO disks(node_id,source_path,volume,uuid,fstype,total,"
                   "free,scanned,remote,folders_only,ignore_json,color,mount,archives,media) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (disk_id, root_path, info["volume"], info["uuid"], info["fstype"],
                    info["total"], info["free"], time.time(), info["remote"],
                    1 if folders_only else 0, json.dumps(sorted(ignore_set)),
                    old[0] if old else "", info["mount"], 1 if archives else 0,
                    1 if media else 0))
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


def _scope_cond(db, scope_node):
    """SQL condition (on alias n) + params restricting to a group/disk/folder/archive."""
    if scope_node is None:
        return "", []
    r = db.execute("SELECT kind,disk_id,relpath FROM nodes WHERE id=?", (scope_node,)).fetchone()
    if not r:
        return "", []
    k, did, rel = r
    if k == KIND_GROUP:
        ids = scope_disk_ids(db, scope_node)
        return "n.disk_id IN (%s)" % (",".join(map(str, ids)) or "NULL"), []
    if k == KIND_DISK:
        return "n.disk_id=?", [did]
    esc = rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    sep = ARC_SEP if k == KIND_ARCHIVE else "/"
    return "n.disk_id=? AND n.relpath LIKE ? ESCAPE '\\'", [did, esc + sep + "%"]


def _ext_cond(exts):
    """Condition matching file names that end in one of the extensions."""
    exts = sorted({e.lower().lstrip(".").strip() for e in exts if e.strip(" .")})
    if not exts:
        return "", []
    return ("(" + " OR ".join("n.name_lc GLOB ?" for _ in exts) + ")",
            ["*." + e.replace("[", "[[]") for e in exts])


def search(db, text="", mode="normal", whole=False, case=False, in_desc=True,
           kinds=(KIND_FILE, KIND_DIR, KIND_DISK, KIND_ARCHIVE), scope_node=None,
           size_op=None, size_val=0, date_from=None, date_to=None, limit=50000,
           in_meta=True, category=None, exts=None, in_archives=True, disk_ids=None):
    """Return a list of tuples
    (id,kind,name,relpath,size,mtime,disk_name,description,disk_id,meta).

    mode: normal (whole text as one phrase) | all (every word) | any (some word).
    Wildcards: * ? # [abc]. scope_node: None = everything; group/disk/folder.
    category: a key of CATEGORIES (files only). exts: extra list of extensions.
    disk_ids: only these disks (None = all).
    """
    cond, par = [], []
    terms = [text] if mode == "normal" else text.split()
    terms = [t for t in terms if t.strip() != ""]
    tconds = []
    for t in terms:
        ncol, dcol = ("name", "description") if case else ("name_lc", "desc_lc")
        g = _glob(t, whole, case)
        alts, p = [], []
        if not case and not whole and _plain(t) and len(t) >= 3:
            alts.append("n.id IN (SELECT rowid FROM fts WHERE fts MATCH ?)")
            p.append('"' + t.lower().replace('"', '""') + '"')
        else:
            alts.append(f"n.{ncol} GLOB ?")
            p.append(g)
        if in_desc:  # only rows that have a description (small partial index)
            alts.append(f"n.id IN (SELECT id FROM nodes WHERE desc_lc<>'' AND {dcol} GLOB ?)")
            p.append(g)
        if in_meta:
            mcol = "meta" if case else "lower(meta)"
            alts.append(f"n.id IN (SELECT id FROM nodes WHERE meta<>'' AND {mcol} GLOB ?)")
            p.append(g if case else g.lower())
        tconds.append("(" + " OR ".join(alts) + ")")
        par.extend(p)
    if tconds:
        cond.append("(" + (" OR " if mode == "any" else " AND ").join(tconds) + ")")
    all_exts = []
    if category and category in CATEGORIES:
        all_exts += CATEGORIES[category].split()
    if exts:
        all_exts += list(exts)
    if all_exts:
        kinds = [k for k in kinds if k in FILE_KINDS] or list(FILE_KINDS)
        c, p = _ext_cond(all_exts)
        if c:
            cond.append(c)
            par += p
    cond.append("n.kind IN (%s)" % (",".join(str(int(k)) for k in kinds) or "-1"))
    if not in_archives:
        cond.append("instr(n.relpath, '%s')=0" % ARC_SEP)
    c, p = _scope_cond(db, scope_node)
    if c:
        cond.append(c)
        par += p
    if disk_ids is not None:
        cond.append("n.disk_id IN (%s)" % (",".join(str(int(d)) for d in disk_ids) or "NULL"))
    if size_op and size_op in (">=", "<=", "="):
        cond.append(f"n.size {size_op} ?")
        par.append(size_val)
    if date_from is not None:
        cond.append("n.mtime >= ?")
        par.append(date_from)
    if date_to is not None:
        cond.append("n.mtime <= ?")
        par.append(date_to)
    sql = ("SELECT n.id,n.kind,n.name,n.relpath,n.size,n.mtime,d.name,n.description,"
           "n.disk_id,n.meta "
           "FROM nodes n LEFT JOIN nodes d ON d.id=n.disk_id WHERE " + " AND ".join(cond) +
           " LIMIT ?")
    par.append(limit)
    return db.execute(sql, par).fetchall()


def path_of(db, node_id):
    """'Disk/folder/sub' path for a node."""
    r = db.execute("SELECT n.relpath,d.name FROM nodes n LEFT JOIN nodes d ON d.id=n.disk_id "
                   "WHERE n.id=?", (node_id,)).fetchone()
    if not r:
        return ""
    rel, dn = r
    return dn + ("/" + display_rel(rel) if rel else "") if dn else ""


# ---------------------------------------------------------------- statistics
def stats(db, scope_node=None, top=100, per_disk=None):
    """Statistics for everything / a group / disk / folder:
    {'files', 'folders', 'size', 'categories': [(cat, count, size)] (biggest first),
     'largest': [(id, name, size, disk_name, relpath, disk_id)], 'per_disk': n or None}
    per_disk: when the scope has several disks, the n largest files OF EACH disk
    (so no disk is left out); otherwise the `top` largest overall."""
    c, p = _scope_cond(db, scope_node)
    where = "n.kind IN (0,4) AND instr(n.relpath, '%s')=0" % ARC_SEP + (" AND " + c if c else "")
    cats = {}
    files = size = 0
    for nm, sz in db.execute(f"SELECT n.name_lc, n.size FROM nodes n WHERE {where}", p):
        cat = EXT_CATEGORY.get(nm.rsplit(".", 1)[-1] if "." in nm else "", "Other")
        cnt, tot = cats.get(cat, (0, 0))
        cats[cat] = (cnt + 1, tot + (sz or 0))
        files += 1
        size += sz or 0
    wf = "n.kind=1 AND instr(n.relpath, '%s')=0" % ARC_SEP + (" AND " + c if c else "")
    folders = db.execute(f"SELECT COUNT(*) FROM nodes n WHERE {wf}", p).fetchone()[0]
    sel = (f"SELECT n.id, n.name, n.size, d.name, n.relpath, n.disk_id FROM nodes n "
           f"LEFT JOIN nodes d ON d.id=n.disk_id WHERE {where}")
    disk_ids = None
    if per_disk:
        if scope_node is None:
            disk_ids = [r[0] for r in db.execute("SELECT node_id FROM disks ORDER BY node_id")]
        else:
            r = db.execute("SELECT kind FROM nodes WHERE id=?", (scope_node,)).fetchone()
            if r and r[0] == KIND_GROUP:
                disk_ids = scope_disk_ids(db, scope_node)
    if disk_ids is not None and len(disk_ids) > 1:
        largest = []
        for did in disk_ids:  # one small query per disk (uses the disk index)
            largest += db.execute(sel + " AND n.disk_id=? ORDER BY n.size DESC LIMIT ?",
                                  p + [did, per_disk]).fetchall()
    else:
        per_disk = None
        largest = db.execute(sel + " ORDER BY n.size DESC LIMIT ?", p + [top]).fetchall()
    return {"files": files, "folders": folders, "size": size,
            "categories": sorted(((k, v[0], v[1]) for k, v in cats.items()),
                                 key=lambda x: -x[2]),
            "largest": largest, "per_disk": per_disk}


# ---------------------------------------------------------------- duplicates
def duplicates(db, min_size=1024 ** 2, across_disks=True, scope_node=None, max_groups=5000):
    """Probable duplicate files: same name (ignoring case) and same size.
    across_disks: only groups that are on at least two different disks.
    Returns [(name, size, [(id, disk_id, disk_name, relpath), ...])], biggest
    wasted space first. Items inside archives are not considered."""
    c, p = _scope_cond(db, scope_node)
    base = "n.kind=0 AND n.size>=? AND instr(n.relpath, '%s')=0" % ARC_SEP + (" AND " + c if c else "")
    having = "COUNT(*)>1" + (" AND COUNT(DISTINCT n.disk_id)>1" if across_disks else "")
    sql = (f"WITH g AS (SELECT n.name_lc AS k, n.size AS s FROM nodes n WHERE {base} "
           f"GROUP BY n.name_lc, n.size HAVING {having} "
           f"ORDER BY n.size*(COUNT(*)-1) DESC LIMIT ?) "
           f"SELECT n.id, n.name, n.size, n.disk_id, d.name, n.relpath, n.name_lc "
           f"FROM g JOIN nodes n ON n.name_lc=g.k AND n.size=g.s "
           f"LEFT JOIN nodes d ON d.id=n.disk_id WHERE {base} "
           f"ORDER BY n.size*1.0 DESC, n.name_lc, d.name_lc, n.relpath")
    groups, order = {}, []
    for nid, nm, sz, did, dn, rel, key in db.execute(sql, [min_size] + p + [max_groups, min_size] + p):
        k = (key, sz)
        if k not in groups:
            groups[k] = (nm, sz, [])
            order.append(k)
        groups[k][2].append((nid, did, dn, rel))
    res = [groups[k] for k in order]
    res.sort(key=lambda g: -(g[1] * (len(g[2]) - 1)))
    return res


def export_csv(db, out_path):
    n = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Disk", "Path", "Name", "Type", "Size", "Modified", "Info", "Description"])
        for dn, rel, nm, kind, sz, mt, ds, meta in db.execute(
                "SELECT d.name,n.relpath,n.name,n.kind,n.size,n.mtime,n.description,n.meta "
                "FROM nodes n JOIN nodes d ON d.id=n.disk_id WHERE n.kind IN (0,1,4) "
                "ORDER BY d.name_lc,n.relpath"):
            w.writerow([dn, display_rel(rel), nm, "folder" if kind == KIND_DIR else "file", sz,
                        fmt_date(mt), meta, ds])
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
                        archives="--archives" in rest, media="--media" in rest,
                        progress=lambda n, p, f=None: print(
                            f"\r{n} items" + (f" ({f * 100:.0f}%)" if f is not None else "") + "...",
                            end="", file=sys.stderr))
        for ep, em, _k in errs:
            print(f"UNREADABLE: {ep}: {em}", file=sys.stderr)
        sz, = db.execute("SELECT size FROM nodes WHERE id=?", (did,)).fetchone()
        cnt, = db.execute("SELECT COUNT(*) FROM nodes WHERE disk_id=?", (did,)).fetchone()
        print(f"\nOK: '{name}' {cnt} items, {fmt_size(sz)}, {time.time()-t:.1f}s")
    elif cmd == "update":
        r = db.execute("SELECT n.id,s.source_path,s.folders_only,s.archives,s.media FROM nodes n "
                       "JOIN disks s ON s.node_id=n.id WHERE n.name=?", (rest[0],)).fetchone()
        if not r:
            print("Disk not found")
            return 1
        errs = []
        scan_disk(db, rest[1] if len(rest) > 1 else r[1], rest[0], update_node=r[0],
                  folders_only=bool(r[2]), archives=bool(r[3]), media=bool(r[4]), errors=errs)
        for ep, em, k in errs:
            print(f"UNREADABLE: {ep}: {em}" + (" (previous data kept)" if k else ""), file=sys.stderr)
        print("Updated" + (f" with {len(errs)} unreadable item(s)" if errs else ""))
    elif cmd == "search":
        t = time.time()
        res = search(db, rest[0], mode=opt("--mode", "normal"), whole="--whole" in rest,
                     case="--case" in rest, category=opt("--type"))
        for _id, k, nm, rel, sz, mt, dn, ds, _did, meta in res[:200]:
            print(f"{dn}/{display_rel(rel)}\t{fmt_size(sz)}\t{fmt_date(mt)}\t{meta}")
        print(f"-- {len(res)} results in {time.time()-t:.3f}s", file=sys.stderr)
    elif cmd == "list":
        for nm, sz, tot, free, sc, src in db.execute(
                "SELECT n.name,n.size,s.total,s.free,s.scanned,s.source_path FROM nodes n "
                "JOIN disks s ON s.node_id=n.id ORDER BY n.name_lc"):
            print(f"{nm}\t{fmt_size(sz)}\t{fmt_size(tot)}\t{fmt_date(sc)}\t{src}")
    elif cmd == "stats":
        scope = None
        if rest:
            r = db.execute("SELECT id FROM nodes WHERE kind=? AND name=?", (KIND_DISK, rest[0])).fetchone()
            scope = r[0] if r else None
        s = stats(db, scope, top=10)
        print(f"{s['files']} files, {s['folders']} folders, {fmt_size(s['size'])}")
        for cat, cnt, sz in s["categories"]:
            print(f"  {cat:<10} {cnt:>9}  {fmt_size(sz)}")
    elif cmd == "dups":
        mb = float(rest[0]) if rest else 1
        for nm, sz, items in duplicates(db, int(mb * 1024 ** 2))[:100]:
            print(f"{nm}  ({fmt_size(sz)}) x{len(items)}")
            for _i, _d, dn, rel in items:
                print(f"    {dn}/{display_rel(rel)}")
    elif cmd == "export-csv":
        print(export_csv(db, rest[0]), "rows")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
