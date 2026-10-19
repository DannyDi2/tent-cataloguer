#!/usr/bin/env python3
"""dcat_gui - PyQt6 front end of the offline disk catalog.

Usage:  python3 dcat_gui.py [catalog.dcat]
"""
import csv
import os
import sqlite3
import sys
import time
import traceback

from PyQt6.QtCore import QDate, QSettings, Qt
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QPalette
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressDialog, QPushButton, QRadioButton, QSplitter, QTabWidget,
    QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import dcat_core as core
import icons as ic
from dcat_core import KIND_DIR, KIND_DISK, KIND_FILE, KIND_GROUP

ID = Qt.ItemDataRole.UserRole
KEY = Qt.ItemDataRole.UserRole + 1
UNDO_LIMIT = 25
MAX_PREFILL = 200
SIGNATURE = "Engineered by Danny/Claude with love, 2026"
SEARCH_COLUMNS = ["Name", "In folder", "Size", "Type", "Modified", "Description"]
SEARCH_DEFAULT_RATIOS = [0.28, 0.34, 0.07, 0.08, 0.11]  # share of the window width; the last column stretches


def settings():
    return QSettings("dcat", "dcat")


def theme_colors(widget):
    """(ink, paper) colors of the current theme, as '#rrggbb' strings."""
    pal = widget.palette()
    return (pal.color(QPalette.ColorRole.WindowText).name(),
            pal.color(QPalette.ColorRole.Window).name())


class SortItem(QTreeWidgetItem):
    def __lt__(self, other):
        col = self.treeWidget().sortColumn()
        a, b = self.data(col, KEY), other.data(col, KEY)
        if a is None or b is None:
            return self.text(col).lower() < other.text(col).lower()
        return a < b


def icon(kind, name="", remote=False):
    if kind == KIND_DISK and remote:
        return ic.icon_disk_remote()
    return ic.icon_for_name(kind, name)


# ---------------------------------------------------------------- signature
class SignatureBadge(QWidget):
    """Bottom-right signature: text plus the black-and-white tent/campfire logo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(8)
        self.text_label = QLabel(SIGNATURE)
        f = self.text_label.font()
        f.setItalic(True)
        if f.pointSizeF() > 8:
            f.setPointSizeF(f.pointSizeF() - 1)
        self.text_label.setFont(f)
        ink, paper = theme_colors(self)
        self.logo_label = QLabel()
        self.logo_label.setPixmap(ic.logo_pixmap(34, ink, paper, dpr=self.devicePixelRatioF()))
        lay.addWidget(self.text_label)
        lay.addWidget(self.logo_label)


# ---------------------------------------------------------------- Read Disk
class ReadDiskDialog(QDialog):
    def __init__(self, win, update_node=None):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Read Disk")
        self.setMinimumWidth(520)
        f = QFormLayout(self)
        self.drive = QComboBox()
        self.drive.setEditable(True)
        for mp, desc in core.mounted_places():
            self.drive.addItem(f"{mp}  -  {desc}", mp)
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda _checked=False: self.browse())
        row = QHBoxLayout()
        row.addWidget(self.drive, 1)
        row.addWidget(browse)
        f.addRow("Drive/Path:", row)
        self.name = QLineEdit()
        self.desc = QLineEdit()
        f.addRow("Name:", self.name)
        f.addRow("Description:", self.desc)
        self.insert = QComboBox()
        self.insert.addItem(win.db_title(), None)
        for nid, _nm, path in win.groups():
            self.insert.addItem(path, nid)
        f.addRow("Insert in:", self.insert)
        self.folders_only = QCheckBox("Read folders only (do not store files)")
        f.addRow("", self.folders_only)
        self.upd = QCheckBox("Update Disk:")
        self.upd_combo = QComboBox()
        for nid, nm in win.disk_list():
            self.upd_combo.addItem(nm, nid)
        f.addRow(self.upd, self.upd_combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.drive.currentIndexChanged.connect(lambda _i: self.autoname())
        if update_node:
            self.upd.setChecked(True)
            self.upd_combo.setCurrentIndex(max(0, self.upd_combo.findData(update_node)))
            r = win.db.execute("SELECT source_path,folders_only FROM disks WHERE node_id=?",
                               (update_node,)).fetchone()
            if r:
                self.drive.setEditText(r[0] or "")
                self.folders_only.setChecked(bool(r[1]))
            self.name.setText(self.upd_combo.currentText())
        else:
            self.autoname()

    def path(self):
        idx = self.drive.currentIndex()
        if idx >= 0 and self.drive.currentText() == self.drive.itemText(idx):
            return self.drive.itemData(idx)
        return self.drive.currentText().strip()

    def autoname(self):
        if self.upd.isChecked():
            return
        p = self.path()
        info = core.disk_info(p) if os.path.isdir(p) else {}
        self.name.setText(info.get("volume") or os.path.basename(p.rstrip("/")) or "root")

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder or disk", "/")
        if d:
            self.drive.setEditText(d)
            self.autoname()


# ------------------------------------------------------------------ Properties
class PropertiesDialog(QDialog):
    def __init__(self, win, node_ids):
        super().__init__(win)
        self.win = win
        self.ids = node_ids
        multi = len(node_ids) > 1
        rows = [win.db.execute("SELECT name,kind,size,mtime,description FROM nodes WHERE id=?",
                               (n,)).fetchone() for n in node_ids]
        title = rows[0][0] if not multi else f"{len(node_ids)} items"
        self.setWindowTitle(f"Properties for {title}")
        self.resize(460, 420)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        gen = QWidget()
        gl = QFormLayout(gen)
        gl.addRow("Name:", QLabel(title))
        if not multi:
            gl.addRow("Type:", QLabel(win.type_name(rows[0][1], rows[0][0])))
            gl.addRow("Size:", QLabel(core.fmt_size(rows[0][2])))
            gl.addRow("Modified:", QLabel(core.fmt_date(rows[0][3])))
            gl.addRow("Location:", QLabel(win.node_path(win.node_row(node_ids[0])[0])))
        else:
            gl.addRow("Size (total):", QLabel(core.fmt_size(sum(r[2] or 0 for r in rows))))
        tabs.addTab(gen, "General")
        desc = QWidget()
        dl = QVBoxLayout(desc)
        self.text = QPlainTextEdit()
        if not multi:
            self.text.setPlainText(rows[0][4] or "")
        dl.addWidget(self.text)
        self.recursive = QCheckBox("Use this description as well for all sub-items")
        dl.addWidget(self.recursive)
        tabs.addTab(desc, "Description")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                              | QDialogButtonBox.StandardButton.Apply)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(lambda _c=False: self.apply())
        lay.addWidget(bb)

    def apply(self):
        txt = self.text.toPlainText()
        self.win.push_undo()
        for nid in self.ids:
            self.win.db.execute("UPDATE nodes SET description=?,desc_lc=? WHERE id=?",
                                (txt, txt.lower(), nid))
            if self.recursive.isChecked():
                self.win.db.execute(
                    """WITH RECURSIVE t(id) AS (SELECT id FROM nodes WHERE parent_id=?
                         UNION ALL SELECT n.id FROM nodes n JOIN t ON n.parent_id=t.id)
                       UPDATE nodes SET description=?,desc_lc=? WHERE id IN (SELECT id FROM t)""",
                    (nid, txt, txt.lower()))
        self.win.mark_dirty()
        self.win.refresh()

    def accept(self):
        self.apply()
        super().accept()


# ------------------------------------------------------------ Ignored items
class IgnoredDialog(QDialog):
    """Lists the folders/files that Update Disk skips for one disk."""

    def __init__(self, win, disk_id):
        super().__init__(win)
        self.win = win
        self.disk_id = disk_id
        self.setWindowTitle("Ignored folders/files on this disk")
        self.resize(520, 380)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("These paths are skipped every time you run Update Disk:"))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.addItems(core.get_ignored(win.db, disk_id))
        lay.addWidget(self.list, 1)
        self.remove_btn = QPushButton("Remove selected (they will be catalogued again on the next Update Disk)")
        self.remove_btn.clicked.connect(lambda _c=False: self.remove_selected())
        lay.addWidget(self.remove_btn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def remove_selected(self):
        chosen = {it.text() for it in self.list.selectedItems()}
        if not chosen:
            return
        keep = [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).text() not in chosen]
        self.win.push_undo()
        core.set_ignored(self.win.db, self.disk_id, keep)
        self.win.mark_dirty()
        self.list.clear()
        self.list.addItems(keep)


# ------------------------------------------------------------------ Search
class SearchDialog(QWidget):
    HEADER_KEY = "search/header"
    GEOMETRY_KEY = "search/geometry"

    def __init__(self, win, scope_node):
        super().__init__(None, Qt.WindowType.Window)
        self.win = win
        self.scope_node = scope_node
        self.setWindowTitle("Search")
        self.setWindowIcon(ic.toolbar_icon("search"))
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        g = QWidget()
        gl = QGridLayout(g)
        self.text = QComboBox()
        self.text.setEditable(True)
        self.text.setMinimumWidth(320)
        self.text.lineEdit().returnPressed.connect(self.run)
        gl.addWidget(QLabel("Search for:"), 0, 0)
        gl.addWidget(self.text, 0, 1, 1, 2)
        gl.addWidget(QLabel("Search in:"), 1, 0)
        self.scope = QComboBox()
        self.scope.addItem("Entire database", None)
        if scope_node:
            self.scope.addItem(win.node_path(scope_node), scope_node)
            self.scope.setCurrentIndex(1)
        gl.addWidget(self.scope, 1, 1, 1, 2)
        opts = QGroupBox("Options")
        ol = QGridLayout(opts)
        self.c_files = QCheckBox("Files")
        self.c_dirs = QCheckBox("File folders")
        self.c_disks = QCheckBox("Disks")
        for c in (self.c_files, self.c_dirs, self.c_disks):
            c.setChecked(True)
        self.c_desc = QCheckBox("Search in description")
        self.c_desc.setChecked(True)
        self.c_whole = QCheckBox("Match whole string only")
        self.c_case = QCheckBox("Case-sensitive")
        ol.addWidget(self.c_files, 0, 0)
        ol.addWidget(self.c_dirs, 1, 0)
        ol.addWidget(self.c_disks, 2, 0)
        ol.addWidget(self.c_desc, 0, 1)
        ol.addWidget(self.c_whole, 1, 1)
        ol.addWidget(self.c_case, 2, 1)
        self.m_normal = QRadioButton("Normal search")
        self.m_all = QRadioButton("All words")
        self.m_any = QRadioButton("Any word")
        self.m_normal.setChecked(True)
        mr = QHBoxLayout()
        for m in (self.m_normal, self.m_all, self.m_any):
            mr.addWidget(m)
        ol.addLayout(mr, 3, 0, 1, 2)
        gl.addWidget(opts, 2, 0, 1, 3)
        self.tabs.addTab(g, "General")
        e = QWidget()
        el = QGridLayout(e)
        el.addWidget(QLabel("Size:"), 0, 0)
        self.size_op = QComboBox()
        for lbl, op in (("(any)", None), ("at least", ">="), ("at most", "<="), ("exactly", "=")):
            self.size_op.addItem(lbl, op)
        self.size_val = QDoubleSpinBox()
        self.size_val.setRange(0, 10**9)
        self.size_val.setDecimals(2)
        self.size_unit = QComboBox()
        for u, m in (("Bytes", 1), ("KB", 1024), ("MB", 1024**2), ("GB", 1024**3), ("TB", 1024**4)):
            self.size_unit.addItem(u, m)
        self.size_unit.setCurrentIndex(2)
        el.addWidget(self.size_op, 0, 1)
        el.addWidget(self.size_val, 0, 2)
        el.addWidget(self.size_unit, 0, 3)
        self.d_from_on = QCheckBox("Modified after:")
        self.d_to_on = QCheckBox("Modified before:")
        self.d_from = QDateEdit(QDate.currentDate().addYears(-1))
        self.d_to = QDateEdit(QDate.currentDate())
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
        el.addWidget(self.d_from_on, 1, 0)
        el.addWidget(self.d_from, 1, 1)
        el.addWidget(self.d_to_on, 2, 0)
        el.addWidget(self.d_to, 2, 1)
        el.setRowStretch(3, 1)
        self.tabs.addTab(e, "Extended")
        br = QHBoxLayout()
        self.start = QPushButton("Start")
        self.start.setIcon(ic.toolbar_icon("search"))
        self.start.clicked.connect(lambda _c=False: self.run())
        self.export = QPushButton("Export CSV...")
        self.export.clicked.connect(lambda _c=False: self.export_csv())
        br.addWidget(self.start)
        br.addWidget(self.export)
        br.addStretch(1)
        lay.addLayout(br)
        self.res = QTreeWidget()
        self.res.setRootIsDecorated(False)
        self.res.setSortingEnabled(True)
        self.res.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.res.setHeaderLabels(SEARCH_COLUMNS)
        self.res.itemDoubleClicked.connect(lambda it, _c: self.open_item(it))
        self.res.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.res.customContextMenuRequested.connect(self.results_menu)
        lay.addWidget(self.res, 1)
        self.status = QLabel("")
        lay.addWidget(self.status)
        self.restore_layout()
        self.prefill_from_clipboard()
        self.text.setFocus()

    # ---- remembered layout (window size/position and column widths/order/sort)
    def restore_layout(self):
        st = settings()
        geo = st.value(self.GEOMETRY_KEY)
        if geo is None or not self.restoreGeometry(geo):
            scr = QApplication.primaryScreen().availableGeometry()
            w, h = int(scr.width() * 0.92), int(scr.height() * 0.82)
            self.resize(w, h)
            self.move(scr.x() + (scr.width() - w) // 2, scr.y() + (scr.height() - h) // 2)
        header = self.res.header()
        state = st.value(self.HEADER_KEY)
        if state is None or not header.restoreState(state):
            for i, ratio in enumerate(SEARCH_DEFAULT_RATIOS):
                self.res.setColumnWidth(i, max(60, int(ratio * self.width())))
            header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.sectionResized.connect(lambda *_a: self.save_layout())
        header.sectionMoved.connect(lambda *_a: self.save_layout())
        header.sortIndicatorChanged.connect(lambda *_a: self.save_layout())

    def save_layout(self):
        st = settings()
        st.setValue(self.HEADER_KEY, self.res.header().saveState())
        st.setValue(self.GEOMETRY_KEY, self.saveGeometry())

    def closeEvent(self, e):
        self.save_layout()
        if self in self.win.searches:
            self.win.searches.remove(self)
        super().closeEvent(e)

    # ---- the last Ctrl+C is pasted into the search box when the window opens
    def prefill_from_clipboard(self):
        for line in QApplication.clipboard().text().splitlines():
            line = line.strip()
            if line:
                self.text.setEditText(line[:MAX_PREFILL])
                self.text.lineEdit().selectAll()
                return

    def run(self):
        kinds = [k for c, k in ((self.c_files, KIND_FILE), (self.c_dirs, KIND_DIR),
                                (self.c_disks, KIND_DISK)) if c.isChecked()]
        mode = "all" if self.m_all.isChecked() else "any" if self.m_any.isChecked() else "normal"
        t0 = time.time()
        date_from = (self.d_from.date().startOfDay().toSecsSinceEpoch()
                     if self.d_from_on.isChecked() else None)
        date_to = (self.d_to.date().endOfDay().toSecsSinceEpoch()
                   if self.d_to_on.isChecked() else None)
        rows = core.search(
            self.win.db, self.text.currentText(), mode=mode, whole=self.c_whole.isChecked(),
            case=self.c_case.isChecked(), in_desc=self.c_desc.isChecked(), kinds=kinds,
            scope_node=self.scope.currentData(), size_op=self.size_op.currentData(),
            size_val=int(self.size_val.value() * self.size_unit.currentData()),
            date_from=date_from, date_to=date_to)
        dt = time.time() - t0
        self.res.setUpdatesEnabled(False)
        self.res.clear()
        self.res.setSortingEnabled(False)
        for nid, kind, name, rel, size, mt, dn, desc in rows:
            where = "" if kind == KIND_DISK else (dn + "/" + rel.rsplit("/", 1)[0] if "/" in rel else dn)
            it = SortItem([name, where, core.fmt_size(size), self.win.type_name(kind, name),
                           core.fmt_date(mt), desc])
            it.setData(0, ID, nid)
            it.setIcon(0, icon(kind, name))
            it.setData(2, KEY, size)
            it.setData(4, KEY, mt or 0)
            it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight)
            self.res.addTopLevelItem(it)
        self.res.setSortingEnabled(True)
        self.res.setUpdatesEnabled(True)
        self.status.setText(f"{len(rows)} item(s) in {dt:.3f}s"
                            + ("  (50000 limit reached)" if len(rows) >= 50000 else ""))
        self.text.lineEdit().selectAll()

    def open_item(self, it):
        self.win.goto(it.data(0, ID))
        self.win.raise_()
        self.win.activateWindow()

    def results_menu(self, pos):
        it = self.res.itemAt(pos)
        if not it:
            return
        nid = it.data(0, ID)
        m = QMenu(self)
        m.addAction(ic.toolbar_icon("open"), "Show in catalog", lambda: self.open_item(it))
        m.addAction(ic.toolbar_icon("open"), "Open file location", lambda: self.win.act_open_location(nid))
        m.addSeparator()
        m.addAction("Copy path to clipboard",
                    lambda: QApplication.clipboard().setText(self.win.node_path(nid)))
        m.addAction(ic.toolbar_icon("properties"), "Properties...",
                    lambda: PropertiesDialog(self.win, [nid]).exec())
        m.exec(self.res.viewport().mapToGlobal(pos))

    def export_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export results", "results.csv", "CSV (*.csv)")
        if not p:
            return
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SEARCH_COLUMNS)
            for i in range(self.res.topLevelItemCount()):
                it = self.res.topLevelItem(i)
                w.writerow([it.text(c) for c in range(len(SEARCH_COLUMNS))])


# ---------------------------------------------------------------- Main window
class MainWindow(QMainWindow):
    def __init__(self, db_path):
        super().__init__()
        self.resize(1250, 720)
        self.hist, self.hpos = [], -1
        self.searches = []
        self.current = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.db_path = None
        self.open_path(db_path, initial=True)
        self.build_ui()
        self.load_root()

    # ---- database / undo / save
    def open_path(self, path, initial=False):
        self.db = core.load_into_memory(path) if path else core.new_memory_db()
        self.db_path = path
        if path:
            settings().setValue("last_db", path)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        if not initial:
            self.update_title()
            self.update_undo_actions()

    def db_title(self):
        if not self.db_path:
            return "Untitled"
        return os.path.splitext(os.path.basename(self.db_path))[0]

    def update_title(self):
        star = " *" if self.dirty else ""
        where = self.db_path or "(new, unsaved)"
        self.setWindowTitle(f"Disk Catalog - {where}{star}")

    def mark_dirty(self):
        self.dirty = True
        self.update_title()

    def push_undo(self):
        """Call BEFORE any change to the database."""
        self.undo_stack.append(core.snapshot(self.db))
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.update_undo_actions()

    def do_undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(core.snapshot(self.db))
        core.restore(self.db, self.undo_stack.pop())
        self.mark_dirty()
        self.after_restore()

    def do_redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(core.snapshot(self.db))
        core.restore(self.db, self.redo_stack.pop())
        self.mark_dirty()
        self.after_restore()

    def after_restore(self):
        self.update_undo_actions()
        cur = self.current
        self.load_root()
        if cur is not None and self.node_row(cur):
            self.select_tree(cur)

    def update_undo_actions(self):
        if hasattr(self, "a_undo"):
            self.a_undo.setEnabled(bool(self.undo_stack))
            self.a_redo.setEnabled(bool(self.redo_stack))

    def maybe_save_prompt(self):
        """True to continue (saved or discarded), False to cancel."""
        if not self.dirty:
            return True
        r = QMessageBox.question(
            self, "Disk Catalog", "You have unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Yes:
            return self.act_save()
        return True

    def groups(self):
        return [(nid, nm, self.node_path(nid)) for nid, nm in
                self.db.execute("SELECT id,name FROM nodes WHERE kind=? ORDER BY name_lc", (KIND_GROUP,))]

    def disk_list(self):
        return self.db.execute("SELECT id,name FROM nodes WHERE kind=? ORDER BY name_lc",
                               (KIND_DISK,)).fetchall()

    def node_path(self, nid):
        parts = []
        while nid is not None:
            r = self.db.execute("SELECT parent_id,name,kind,relpath FROM nodes WHERE id=?",
                                (nid,)).fetchone()
            if not r:
                break
            pid, nm, kind, rel = r
            if kind in (KIND_GROUP, KIND_DISK):
                parts.append(nm)
                nid = pid
            else:
                d = self.db.execute(
                    "SELECT parent_id,name FROM nodes WHERE id=(SELECT disk_id FROM nodes WHERE id=?)",
                    (nid,)).fetchone()
                parts.append(rel)
                parts.append(d[1])
                nid = d[0]
        return "\\".join(reversed(parts)).replace("/", "\\")

    def type_name(self, kind, name=""):
        if kind == KIND_DIR:
            return "File Folder"
        if kind == KIND_GROUP:
            return "Folder"
        if kind == KIND_DISK:
            return "Disk"
        ext = os.path.splitext(name)[1][1:].upper()
        return f"{ext} File" if ext else "File"

    # ---- UI
    def make_action(self, text, slot, shortcut=None, icon_name=None, menu=None, app_wide=False):
        """QAction whose slot is called WITHOUT the 'checked' argument that Qt
        would otherwise pass (it used to leak into slots with optional parameters)."""
        a = QAction(text, self)
        if icon_name:
            a.setIcon(ic.toolbar_icon(icon_name))
        a.triggered.connect(lambda _checked=False, s=slot: s())
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(x) for x in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
            if app_wide:
                a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        if menu is not None:
            menu.addAction(a)
        return a

    def toolbar_button(self, action, extra_actions):
        """Toolbar button that runs `action` on click and offers `extra_actions`
        in a small drop-down. The action itself carries no menu, so it keeps
        working as a normal File/Edit menu entry and keeps its shortcut."""
        btn = QToolButton()
        btn.setDefaultAction(action)
        btn.setAutoRaise(True)
        menu = QMenu(btn)
        for a in extra_actions:
            menu.addAction(a)
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        return btn

    def build_ui(self):
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        em = mb.addMenu("&Edit")
        vm = mb.addMenu("&View")
        hm = mb.addMenu("&Help")
        act = self.make_action

        self.a_new = act("New database", self.act_new, "Ctrl+N", None, fm)
        self.a_open = act("Open...", self.act_open, "Ctrl+O", "open", fm)
        self.a_save = act("Save", self.act_save, "Ctrl+S", "save", fm, app_wide=True)
        self.a_save_as = act("Save As...", self.act_save_as, "Ctrl+Shift+S", None, fm)
        fm.addSeparator()
        act("Export whole database to CSV...", self.act_export_all, None, None, fm)
        self.a_export_list = act("Export current list to CSV...", self.act_export_list, "Ctrl+P", "print", fm)
        fm.addSeparator()
        act("Exit", self.close, "Alt+F4", None, fm)

        self.a_read = act("Add Disk/Folder... (Read Disk)", self.act_read, "Ctrl+R", "add_disk", em)
        self.a_update = act("Update Disk...", self.act_update, "Ctrl+U", "refresh", em)
        self.a_mkdir = act("New Folder (organize catalog)", self.act_mkfolder, None, "new_folder", em)
        self.a_del = act("Delete", self.act_delete, "Del", "delete", em)
        em.addSeparator()
        self.a_search = act("Search...", self.act_search, "Ctrl+F", "search", em)
        self.a_locate = act("Open file location", self.act_open_location, None, "open", em)
        self.a_props = act("Properties...", self.act_properties, "Alt+Return", "properties", em)
        em.addSeparator()
        self.a_undo = act("Undo", self.do_undo, "Ctrl+Z", "undo", em)
        self.a_redo = act("Redo", self.do_redo, ["Ctrl+Y", "Ctrl+Shift+Z"], "redo", em)
        self.a_undo.setEnabled(False)
        self.a_redo.setEnabled(False)

        self.a_back = act("Back", self.go_back, "Alt+Left", "back", vm)
        self.a_fwd = act("Forward", self.go_fwd, "Alt+Right", "forward", vm)
        self.a_up = act("Up", self.go_up, "Backspace", "up", vm)
        act("About", self.act_about, None, None, hm)

        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(self.a_open)
        tb.addWidget(self.toolbar_button(self.a_save, [self.a_save_as]))
        tb.addSeparator()
        tb.addAction(self.a_export_list)
        tb.addSeparator()
        tb.addWidget(self.toolbar_button(self.a_read, [self.a_mkdir]))
        tb.addAction(self.a_update)
        tb.addAction(self.a_del)
        tb.addSeparator()
        tb.addAction(self.a_search)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        tb.addAction(self.a_back)
        tb.addAction(self.a_fwd)
        tb.addAction(self.a_up)

        self.addr = QLineEdit()
        self.addr.setReadOnly(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self.tree_expand)
        self.tree.currentItemChanged.connect(self.tree_select)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.lst = QTreeWidget()
        self.lst.setRootIsDecorated(False)
        self.lst.setSortingEnabled(True)
        self.lst.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.lst.setHeaderLabels(["Name", "Size", "Type", "Modified", "Description"])
        self.lst.setColumnWidth(0, 420)
        self.lst.setColumnWidth(1, 90)
        self.lst.setColumnWidth(2, 110)
        self.lst.setColumnWidth(3, 150)
        self.lst.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.lst.itemDoubleClicked.connect(lambda it, _c: self.goto(it.data(0, ID)))
        self.lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lst.customContextMenuRequested.connect(self.list_menu)
        self.lst.itemSelectionChanged.connect(self.update_status)
        sp_ = QSplitter()
        sp_.addWidget(self.tree)
        sp_.addWidget(self.lst)
        sp_.setStretchFactor(1, 1)
        sp_.setSizes([300, 950])
        c = QWidget()
        v = QVBoxLayout(c)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.addr)
        v.addWidget(sp_, 1)
        self.setCentralWidget(c)
        self.st_items = QLabel("")
        self.st_size = QLabel("")
        self.st_info = QLabel("")
        f_info = self.st_info.font()
        f_info.setPointSizeF(max(f_info.pointSizeF() - 1.5, 6.0))
        self.st_info.setFont(f_info)
        self.statusBar().addWidget(self.st_items, 1)
        self.statusBar().addWidget(self.st_size, 1)
        self.statusBar().addWidget(self.st_info, 4)
        self.badge = SignatureBadge()
        self.statusBar().addPermanentWidget(self.badge)
        self.update_title()

    def act_about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("About Tent Cataloguer for Linux")
        lay = QVBoxLayout(dlg)
        ink, paper = theme_colors(dlg)
        logo = QLabel()
        logo.setPixmap(ic.logo_pixmap(120, ink, paper, dpr=dlg.devicePixelRatioF()))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(logo)
        info = QLabel("<b>Tent Cataloguer for Linux</b><br>Offline catalog of external and local disks.<br>"
                      "Database: SQLite (.dcat)")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)
        sig = QLabel(SIGNATURE)
        f = sig.font()
        f.setItalic(True)
        sig.setFont(f)
        sig.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sig)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    # ---- tree
    def make_tree_item(self, nid, name, kind, remote=False, has_sub=True):
        it = QTreeWidgetItem([name])
        it.setData(0, ID, nid)
        it.setIcon(0, icon(kind, name, remote))
        if has_sub:
            it.addChild(QTreeWidgetItem(["..."]))
        return it

    def has_subfolders(self, nid):
        return self.db.execute("SELECT 1 FROM nodes WHERE parent_id IS ? AND kind IN (1,2,3) LIMIT 1",
                               (nid,)).fetchone() is not None

    def fill_children(self, item):
        nid = item.data(0, ID)
        item.takeChildren()
        for cid, nm, kind, rem in self.db.execute(
                "SELECT n.id,n.name,n.kind,COALESCE(d.remote,0) FROM nodes n LEFT JOIN disks d "
                "ON d.node_id=n.id WHERE n.parent_id IS ? AND n.kind IN (1,2,3) ORDER BY n.kind DESC, n.name_lc",
                (nid,)):
            item.addChild(self.make_tree_item(cid, nm, kind, bool(rem), self.has_subfolders(cid)))

    def load_root(self):
        self.tree.clear()
        root = self.make_tree_item(None, self.db_title(), KIND_GROUP, has_sub=False)
        self.tree.addTopLevelItem(root)
        self.fill_children(root)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)

    def tree_expand(self, item):
        if item.childCount() == 1 and item.child(0).text(0) == "...":
            self.fill_children(item)

    def tree_select(self, cur, _prev):
        if cur is not None:
            self.show_folder(cur.data(0, ID))

    def tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        m = QMenu(self)
        m.addAction(ic.toolbar_icon("add_disk"), "Add Disk/Folder...", self.act_read)
        m.addAction(ic.toolbar_icon("new_folder"), "New Folder", self.act_mkfolder)
        if it:
            nid = it.data(0, ID)
            kind = self.node_row(nid)[1] if nid is not None else KIND_GROUP
            if kind in (KIND_DISK, KIND_DIR):
                m.addAction(ic.toolbar_icon("open"), "Open file location",
                            lambda: self.act_open_location(nid))
            if kind == KIND_DISK:
                m.addAction(ic.toolbar_icon("refresh"), "Update Disk...", lambda: self.act_update(nid))
                m.addAction("Ignored folders...", lambda: self.act_manage_ignored(nid))
            if kind in (KIND_DISK, KIND_GROUP) and nid is not None:
                m.addSeparator()
                m.addAction(ic.toolbar_icon("delete"), "Delete", lambda: self.delete_nodes([nid]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def delete_nodes(self, ids):
        """Delete catalog disks/folders given directly by id (used by the tree
        menu, where there is no selection in the central list)."""
        rows = [(nid, self.node_row(nid)[2], self.node_row(nid)[1]) for nid in ids]
        names = "\n".join(nm for _, nm, _ in rows)
        if QMessageBox.question(self, "Delete", "Remove from catalog?\n\n" + names
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.push_undo()
        for nid, _, kind in rows:
            (core.delete_disk if kind == KIND_DISK else core.delete_group)(self.db, nid)
        self.mark_dirty()
        self.refresh()

    # ---- list
    def show_folder(self, nid, push=True, select=None):
        self.current = nid
        if push:
            self.hist = self.hist[: self.hpos + 1] + [nid]
            self.hpos = len(self.hist) - 1
        self.addr.setText(self.db_title() + ("\\" + self.node_path(nid) if nid is not None else "") + "\\")
        self.lst.setUpdatesEnabled(False)
        self.lst.clear()
        sel_item = None
        self.lst.setSortingEnabled(False)
        for cid, kind, nm, size, mt, ds, rem in self.db.execute(
                "SELECT n.id,n.kind,n.name,n.size,n.mtime,n.description,COALESCE(d.remote,0) FROM nodes n "
                "LEFT JOIN disks d ON d.node_id=n.id WHERE n.parent_id IS ?", (nid,)):
            tname = ("Network Drive" if rem else "Local Disk") if kind == KIND_DISK else self.type_name(kind, nm)
            it = SortItem([nm, core.fmt_size(size) if kind != KIND_GROUP else "", tname,
                           core.fmt_date(mt), ds])
            it.setData(0, ID, cid)
            it.setIcon(0, icon(kind, nm, bool(rem)))
            it.setData(0, KEY, (0 if kind != KIND_FILE else 1, nm.lower()))
            it.setData(1, KEY, size)
            it.setData(3, KEY, mt or 0)
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
            self.lst.addTopLevelItem(it)
            if select == cid:
                sel_item = it
        self.lst.setSortingEnabled(True)
        self.lst.setUpdatesEnabled(True)
        if sel_item:
            self.lst.setCurrentItem(sel_item)
        self.update_status()
        self.a_back.setEnabled(self.hpos > 0)
        self.a_fwd.setEnabled(self.hpos < len(self.hist) - 1)

    def update_status(self):
        n = self.lst.topLevelItemCount()
        sel = self.lst.selectedItems()
        self.st_items.setText(f"{n} item(s)" if not sel else f"{len(sel)} of {n} selected")
        tot = sum((it.data(1, KEY) or 0) for it in (sel or
                  [self.lst.topLevelItem(i) for i in range(n)]))
        self.st_size.setText(core.fmt_size(tot))
        info = ""
        if self.current is not None:
            r = self.db.execute(
                "SELECT s.source_path,s.total,s.free,s.scanned,n.name FROM disks s "
                "JOIN nodes n ON n.id=s.node_id WHERE s.node_id=(SELECT disk_id FROM nodes WHERE id=?)",
                (self.current,)).fetchone()
            if r:
                sep = "     |     "
                info = sep.join([
                    f"{r[4]}",
                    f"Total: {core.fmt_size(r[1])}",
                    f"Free: {core.fmt_size(r[2])}",
                    f"Time: {core.fmt_date(r[3])}",
                ])
        self.st_info.setText(info)

    def node_row(self, nid):
        return self.db.execute("SELECT parent_id,kind,name,disk_id FROM nodes WHERE id=?", (nid,)).fetchone()

    def goto(self, nid):
        r = self.node_row(nid)
        if not r:
            return
        pid, kind, _name, _did = r
        if kind == KIND_FILE:
            self.select_tree(pid)
            self.show_folder(pid, select=nid)
        else:
            self.select_tree(nid)

    def select_tree(self, nid):
        chain = []
        x = nid
        while x is not None:
            chain.append(x)
            x = self.node_row(x)[0]
        chain.reverse()
        item = self.tree.topLevelItem(0)
        for cid in chain:
            self.tree_expand(item)
            item.setExpanded(True)
            found = None
            for i in range(item.childCount()):
                if item.child(i).data(0, ID) == cid:
                    found = item.child(i)
                    break
            if found is None:
                break
            item = found
        self.tree.setCurrentItem(item)
        if nid is None:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def go_back(self):
        if self.hpos > 0:
            self.hpos -= 1
            self._nav()

    def go_fwd(self):
        if self.hpos < len(self.hist) - 1:
            self.hpos += 1
            self._nav()

    def _nav(self):
        nid = self.hist[self.hpos]
        self.tree.blockSignals(True)
        self.select_tree(nid)
        self.tree.blockSignals(False)
        self.show_folder(nid, push=False)

    def go_up(self):
        if self.current is not None:
            self.select_tree(self.node_row(self.current)[0])

    # ---- actions
    def list_menu(self, pos):
        it = self.lst.itemAt(pos)
        m = QMenu(self)
        if it:
            nid = it.data(0, ID)
            kind = self.node_row(nid)[1]
            m.addAction(ic.toolbar_icon("open"), "Open", lambda: self.goto(nid))
            if kind != KIND_GROUP:
                m.addAction(ic.toolbar_icon("open"), "Open file location",
                            lambda: self.act_open_location(nid))
            m.addSeparator()
            m.addAction(ic.toolbar_icon("search"), "Search...", self.act_search)
            m.addSeparator()
            m.addAction("Copy path to clipboard", lambda: QApplication.clipboard().setText(
                self.node_path(nid)))
            m.addAction(ic.toolbar_icon("properties"), "Properties...", self.act_properties)
            if kind == KIND_DISK:
                m.addAction(ic.toolbar_icon("refresh"), "Update Disk...", lambda: self.act_update(nid))
                m.addAction("Ignored folders...", lambda: self.act_manage_ignored(nid))
            m.addSeparator()
            m.addAction(ic.toolbar_icon("delete"), "Delete", self.act_delete)
        else:
            m.addAction(ic.toolbar_icon("add_disk"), "Add Disk/Folder...", self.act_read)
            m.addAction(ic.toolbar_icon("new_folder"), "New Folder", self.act_mkfolder)
        m.exec(self.lst.viewport().mapToGlobal(pos))

    def act_open_location(self, nid=None):
        """Show the item in the file manager (Nemo), selected when possible."""
        if type(nid) is not int:
            sel = self.lst.selectedItems()
            nid = sel[0].data(0, ID) if sel else self.current
        path = core.node_real_path(self.db, nid) if nid is not None else None
        if not path:
            QMessageBox.information(self, "Open file location",
                                    "This item has no location on disk (it is a catalog folder).")
            return
        ok, msg = core.reveal_in_file_manager(path)
        if not ok:
            QMessageBox.warning(self, "Open file location", msg)

    def act_new(self):
        if not self.maybe_save_prompt():
            return
        self.hist, self.hpos = [], -1
        self.open_path(None)
        self.load_root()

    def act_open(self):
        if not self.maybe_save_prompt():
            return
        p, _ = QFileDialog.getOpenFileName(self, "Open database", "", "Catalog (*.dcat)")
        if p:
            try:
                self.open_path(p)
            except sqlite3.DatabaseError as ex:
                QMessageBox.critical(self, "Open database", f"This is not a valid catalog:\n{p}\n\n{ex}")
                return
            self.hist, self.hpos = [], -1
            self.load_root()

    def act_save(self):
        if not self.db_path:
            return self.act_save_as()
        try:
            core.save_to_file(self.db, self.db_path)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.critical(self, "Save", str(ex))
            return False
        self.dirty = False
        settings().setValue("last_db", self.db_path)
        self.update_title()
        self.statusBar().showMessage("Saved.", 3000)
        return True

    def act_save_as(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save As", self.db_path or "catalog.dcat",
                                           "Catalog (*.dcat)")
        if not p:
            return False
        if not p.endswith(".dcat"):
            p += ".dcat"
        self.db_path = p
        return self.act_save()

    def target_group(self):
        cur = self.current
        while cur is not None and self.node_row(cur)[1] != KIND_GROUP:
            cur = self.node_row(cur)[0]
        return cur

    def act_mkfolder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Name of the (organizing) folder that groups disks:")
        if ok and name.strip():
            self.push_undo()
            core.make_group(self.db, name.strip(), self.target_group())
            self.mark_dirty()
            self.refresh()

    def act_read(self):
        self.read_dialog(None)

    def act_update(self, nid=None):
        if type(nid) is not int:
            nid = None
            if self.current is not None:
                d = self.node_row(self.current)[3]
                nid = d if d else None
            if nid is None:
                sel = self.lst.selectedItems()
                if sel and self.node_row(sel[0].data(0, ID))[1] == KIND_DISK:
                    nid = sel[0].data(0, ID)
        self.read_dialog(nid)

    def read_dialog(self, update_node):
        dlg = ReadDiskDialog(self, update_node)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path = dlg.path()
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Read Disk", f"Folder not found:\n{path}")
            return
        upd = dlg.upd_combo.currentData() if dlg.upd.isChecked() else None
        self.push_undo()
        scan_errors = []
        pd = QProgressDialog("Cataloging...", "Cancel", 0, 0, self)
        pd.setWindowModality(Qt.WindowModality.WindowModal)
        pd.setMinimumDuration(0)
        pd.show()

        def progress(n, p):
            if n % 500 == 0:
                pd.setLabelText(f"{n} items\n{p[-80:]}")
                QApplication.processEvents()

        try:
            did = core.scan_disk(self.db, path, dlg.name.text().strip() or "disk",
                                 parent_id=dlg.insert.currentData(), update_node=upd,
                                 folders_only=dlg.folders_only.isChecked(),
                                 progress=progress, cancel=pd.wasCanceled,
                                 errors=scan_errors)
            if dlg.desc.text().strip():
                self.db.execute("UPDATE nodes SET description=?,desc_lc=? WHERE id=?",
                                (dlg.desc.text(), dlg.desc.text().lower(), did))
        except core.Cancelled:
            pd.close()
            self.undo_stack.pop()  # nothing relevant changed; keep the history clean
            self.update_undo_actions()
            self.statusBar().showMessage("Cancelled.", 4000)
            return
        except Exception as ex:  # noqa: BLE001
            pd.close()
            self.undo_stack.pop()
            self.update_undo_actions()
            QMessageBox.critical(self, "Read Disk", str(ex))
            return
        pd.close()
        self.mark_dirty()
        self.refresh()
        self.select_tree(did)
        self.show_scan_errors(scan_errors)

    def show_scan_errors(self, errors):
        """Report items that could not be read; the scan itself has finished."""
        if not errors:
            return
        kept = sum(1 for e in errors if e[2])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Read Disk")
        box.setText(f"Finished, but {len(errors)} item(s) could not be read.\n"
                    f"For {kept} of them the previously catalogued content was kept.")
        lines = [f"{p}\n    {m}" + ("" if k else "   [no previous data]") for p, m, k in errors[:500]]
        if len(errors) > 500:
            lines.append(f"... and {len(errors) - 500} more")
        box.setDetailedText("\n".join(lines))
        box.exec()

    def act_delete(self):
        sel = self.lst.selectedItems()
        rows = [(it.data(0, ID), it.text(0), self.node_row(it.data(0, ID))[1]) for it in sel]
        top_level = [(nid, nm) for nid, nm, k in rows if k in (KIND_DISK, KIND_GROUP)]
        inner = [(nid, nm) for nid, nm, k in rows if k in (KIND_DIR, KIND_FILE)]
        if not top_level and not inner:
            return
        parts = []
        if top_level:
            parts.append("Remove from catalog:\n" + "\n".join(n for _, n in top_level))
        if inner:
            parts.append("Remove from catalog AND ignore on future updates of this disk:\n"
                         + "\n".join(n for _, n in inner))
        if QMessageBox.question(self, "Delete", "\n\n".join(parts)
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.push_undo()
        for nid, _ in top_level:
            kind = self.node_row(nid)[1]
            (core.delete_disk if kind == KIND_DISK else core.delete_group)(self.db, nid)
        for nid, _ in inner:
            core.delete_and_ignore(self.db, nid)
        self.mark_dirty()
        self.refresh()

    def act_manage_ignored(self, disk_id):
        IgnoredDialog(self, disk_id).exec()

    def act_properties(self):
        sel = self.lst.selectedItems()
        ids = [it.data(0, ID) for it in sel]
        if not ids and self.current is not None:
            ids = [self.current]
        if not ids:
            return
        PropertiesDialog(self, ids).exec()

    def act_search(self):
        sel = self.lst.selectedItems()
        scope = self.current
        if sel and self.node_row(sel[0].data(0, ID))[1] != KIND_FILE:
            scope = sel[0].data(0, ID)
        d = SearchDialog(self, scope)
        self.searches.append(d)
        d.show()

    def act_export_all(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export everything", "catalog.csv", "CSV (*.csv)")
        if p:
            n = core.export_csv(self.db, p)
            self.statusBar().showMessage(f"{n} rows exported.", 5000)

    def act_export_list(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export list", "list.csv", "CSV (*.csv)")
        if not p:
            return
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Name", "Size", "Type", "Modified", "Description"])
            for i in range(self.lst.topLevelItemCount()):
                it = self.lst.topLevelItem(i)
                w.writerow([it.text(c) for c in range(5)])

    def refresh(self):
        cur = self.current
        self.load_root()
        if cur is not None and self.node_row(cur):
            self.select_tree(cur)

    def closeEvent(self, e):
        if not self.maybe_save_prompt():
            e.ignore()
            return
        for s in list(self.searches):
            s.close()
        super().closeEvent(e)


def default_catalog_path():
    """Where the catalog lives when nothing else is known. An existing catalog
    in the location used by earlier versions is kept, so no data is 'lost'."""
    legacy = os.path.expanduser("~/Catalogos/catalogo.dcat")  # earlier versions
    if os.path.exists(legacy):
        return legacy
    folder = os.path.expanduser("~/Catalogs")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "catalog.dcat")


def startup_catalog_path(argv):
    """Catalog to open at start: command-line argument, else the last one used,
    else the default location."""
    if len(argv) > 1:
        return argv[1]
    return settings().value("last_db", "") or default_catalog_path()


def install_crash_guard(get_window):
    """Show unexpected errors in a dialog instead of letting PyQt abort the whole
    program (which would lose unsaved, in-memory changes). When there are unsaved
    changes a recovery copy is written next to the catalog."""
    def hook(exc_type, exc, tb):
        sys.stderr.write("".join(traceback.format_exception(exc_type, exc, tb)))
        win = get_window()
        extra = ""
        if win is not None and win.dirty:
            try:
                base = win.db_path or os.path.expanduser("~/unsaved_catalog.dcat")
                rec = os.path.splitext(base)[0] + ".recovery.dcat"
                core.save_to_file(win.db, rec)
                extra = f"\n\nYour unsaved changes were stored in:\n{rec}\n(open it with File > Open)"
            except Exception:  # noqa: BLE001
                extra = "\n\n(Could not write a recovery copy.)"
        try:
            QMessageBox.critical(win, "Unexpected error", f"{exc_type.__name__}: {exc}{extra}")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = hook


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Disk Catalog")
    app.setWindowIcon(QIcon(ic.logo_pixmap(64, "#111111", "#ffffff", dpr=1.0)))
    path = startup_catalog_path(sys.argv)
    try:
        w = MainWindow(path)
    except sqlite3.DatabaseError as ex:
        QMessageBox.critical(None, "Disk Catalog", f"Cannot open the catalog:\n{path}\n\n{ex}")
        sys.exit(1)
    install_crash_guard(lambda: w)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
