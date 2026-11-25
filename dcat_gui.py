#!/usr/bin/env python3
"""dcat_gui - PyQt6 front end of the offline disk catalog (Tent Cataloguer).

Usage:  python3 dcat_gui.py [catalog.dcat]
"""
import csv
import os
import sqlite3
import sys
import time
import traceback

from PyQt6.QtCore import QDate, QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QScrollArea, QSplitter,
    QTabWidget, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import dcat_core as core
import icons as ic
from dcat_core import KIND_ARCHIVE, KIND_DIR, KIND_DISK, KIND_FILE, KIND_GROUP

APP_NAME = "Tent Cataloguer"
APP_VERSION = "2.0.8"
APP_AUTHOR = "Danny"
APP_LICENSE = "GPL-3.0"
APP_HOMEPAGE = "https://github.com/DannyDi2/tent-cataloguer"
ID = Qt.ItemDataRole.UserRole
KEY = Qt.ItemDataRole.UserRole + 1
UNDO_LIMIT = 20
UNDO_MAX_BYTES = 3 * 1024 ** 3   # never keep more than ~3 GB of undo/redo snapshots
MAX_PREFILL = 200
LIVE_LIMIT = 1000        # results shown while typing (Enter shows up to FULL_LIMIT)
FULL_LIMIT = 50000
SIGNATURE = "Engineered by Danny and Claude for you with love"
SEARCH_COLUMNS = ["Name", "In folder", "Size", "Type", "Modified", "Info", "Description"]
SEARCH_DEFAULT_RATIOS = [0.24, 0.30, 0.07, 0.08, 0.10, 0.14]  # share of the width; last column stretches
CATEGORY_COLORS = {
    "Video": "#e74c3c", "Audio": "#9b59b6", "Images": "#16a085", "Documents": "#2f6fd6",
    "E-books": "#c27c0e", "Archives": "#e67e22", "Programs": "#546e7a", "Code": "#00897b",
    "Subtitles": "#7f8c8d", "Other": "#a0aab4",
}
DARK = [False]  # set by apply_theme()


def settings():
    return QSettings("dcat", "dcat")


def theme_colors(widget):
    """(ink, paper) colors of the current theme, as '#rrggbb' strings."""
    pal = widget.palette()
    return (pal.color(QPalette.ColorRole.WindowText).name(),
            pal.color(QPalette.ColorRole.Window).name())


# -------------------------------------------------------------------- theme
LIGHT = dict(window="#f3f5f9", base="#ffffff", alt="#f8fafc", text="#1d2733", muted="#5b6878",
             line="#dfe4ec", head="#eef2f7", accent="#1f8a63", accent_soft="#e3f3ec",
             accent_press="#c8e8da", banner="#fff6d6", banner_line="#f0d58a")
DARKC = dict(window="#1b1f25", base="#22272e", alt="#262c34", text="#e3e8ef", muted="#9aa6b4",
             line="#363d47", head="#2a3038", accent="#35b383", accent_soft="#21382f",
             accent_press="#285443", banner="#3a3320", banner_line="#6b5a26")


def apply_theme(app):
    """Modern flat look (Fusion + palette + a light style sheet). The system
    light/dark preference is kept. No per-item style rules, so big lists stay fast."""
    dark = app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    DARK[0] = dark
    c = DARKC if dark else LIGHT
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: c["window"], QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["base"], QPalette.ColorRole.AlternateBase: c["alt"],
        QPalette.ColorRole.Text: c["text"], QPalette.ColorRole.Button: c["base"],
        QPalette.ColorRole.ButtonText: c["text"], QPalette.ColorRole.Highlight: c["accent"],
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.ToolTipBase: c["base"],
        QPalette.ColorRole.ToolTipText: c["text"], QPalette.ColorRole.PlaceholderText: c["muted"],
        QPalette.ColorRole.Link: c["accent"],
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["muted"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["muted"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c["muted"]))
    app.setPalette(pal)
    ic.set_ink("#cfd8e3" if dark else "#37474f")
    app.setStyleSheet(f"""
QToolBar {{ background: {c['base']}; border: none; border-bottom: 1px solid {c['line']};
           padding: 4px 6px; spacing: 2px; }}
QToolBar::separator {{ background: {c['line']}; width: 1px; margin: 6px 6px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 7px; padding: 3px 5px; }}
QToolButton:hover {{ background: {c['accent_soft']}; border-color: {c['accent_soft']}; }}
QToolButton:pressed, QToolButton:checked {{ background: {c['accent_press']}; }}
QToolButton#chip {{ border: 1px solid {c['line']}; border-radius: 11px; padding: 2px 10px; }}
QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox, QPlainTextEdit {{
    border: 1px solid {c['line']}; border-radius: 6px; padding: 4px 7px; background: {c['base']}; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {c['accent']}; }}
QLineEdit#bigsearch {{ font-size: 15px; padding: 7px 10px; border-radius: 9px;
                       border: 2px solid {c['accent']}; }}
QLineEdit#address {{ background: {c['alt']}; color: {c['muted']}; }}
QTreeView, QListView {{ border: 1px solid {c['line']}; border-radius: 8px; background: {c['base']};
                        alternate-background-color: {c['alt']}; }}
QHeaderView::section {{ background: {c['head']}; color: {c['muted']}; border: none;
    border-right: 1px solid {c['line']}; border-bottom: 1px solid {c['line']};
    padding: 5px 8px; font-weight: 600; }}
QPushButton {{ border: 1px solid {c['line']}; border-radius: 7px; padding: 5px 14px;
               background: {c['base']}; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:default, QPushButton#primary {{ background: {c['accent']}; color: white;
                                           border-color: {c['accent']}; }}
QStatusBar {{ background: {c['base']}; border-top: 1px solid {c['line']}; color: {c['muted']}; }}
QTabWidget::pane {{ border: 1px solid {c['line']}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ padding: 6px 14px; border: none; color: {c['muted']}; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
QGroupBox {{ border: 1px solid {c['line']}; border-radius: 8px; margin-top: 12px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['accent']};
                    font-weight: 600; }}
QSplitter::handle {{ background: transparent; }}
QFrame#banner {{ background: {c['banner']}; border: 1px solid {c['banner_line']}; border-radius: 8px; }}
QFrame#card {{ background: {c['base']}; border: 1px solid {c['line']}; border-radius: 10px; }}
QLabel#cardvalue {{ font-size: 20px; font-weight: 700; color: {c['accent']}; }}
QLabel#cardlabel {{ color: {c['muted']}; }}
QLabel#muted {{ color: {c['muted']}; }}
QProgressBar {{ border: none; border-radius: 4px; background: {c['head']}; max-height: 10px; }}
QMenu {{ background: {c['base']}; border: 1px solid {c['line']}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 26px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 8px; }}
""")
    return dark


def row_color(color):
    """Background for a disk's rows: the disk color softly blended into the
    list background (discreet, but still easy to tell apart)."""
    col = QColor(color or "#ffffff")
    base = QColor(DARKC["base"] if DARK[0] else LIGHT["base"])
    a = 0.32 if DARK[0] else 0.55
    return QColor(int(base.red() * (1 - a) + col.red() * a),
                  int(base.green() * (1 - a) + col.green() * a),
                  int(base.blue() * (1 - a) + col.blue() * a))


def fmt_elapsed(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def fmt_left(sec):
    """Rounded, friendly remaining time."""
    if sec < 60:
        return "1 min" if sec > 40 else "less than a minute"
    if sec < 3600:
        return f"{round(sec / 60)} min"
    h, m = int(sec // 3600), round(sec % 3600 / 60)
    return f"{h} h {m:02d} min"


TIE = Qt.ItemDataRole.UserRole + 2  # tie-breaker kept in the same direction whatever the order


class SortItem(QTreeWidgetItem):
    """Row that sorts by a hidden key per column (sizes, dates...) instead of the
    shown text. Equal keys are ordered by the TIE key, always ascending - so
    'sort by Disk' keeps each disk's biggest files first, in both directions."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn()
        a, b = self.data(col, KEY), other.data(col, KEY)
        if a is None or b is None:
            a, b = self.text(col).lower(), other.text(col).lower()
        try:
            if a != b:
                return a < b
        except TypeError:
            return str(a) < str(b)
        ta, tb = self.data(0, TIE), other.data(0, TIE)
        if ta is None or tb is None or ta == tb:
            return False
        desc = self.treeWidget().header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        return ta > tb if desc else ta < tb


def icon(kind, name="", remote=False, color=None):
    if kind == KIND_DISK:
        if color:
            return ic.icon_disk(color, bool(remote))
        if remote:
            return ic.icon_disk_remote()
    return ic.icon_for_name(kind, name)


def paint_row(item, brush, ncols):
    for c in range(ncols):
        item.setBackground(c, brush)


WINDOW_FLAGS = (Qt.WindowType.Window | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinMaxButtonsHint
                | Qt.WindowType.WindowCloseButtonHint)


def normal_window(w):
    """Give a window the usual minimize / maximize / close buttons (and
    double-click-to-maximize), like any normal Linux application window."""
    w.setWindowFlags(WINDOW_FLAGS)
    if hasattr(w, "setSizeGripEnabled"):
        w.setSizeGripEnabled(True)


def fit_to_screen(w, wf, hf):
    """Size a window to a share of the screen, centered."""
    scr = QApplication.primaryScreen().availableGeometry()
    ww, hh = int(scr.width() * wf), int(scr.height() * hf)
    w.resize(ww, hh)
    w.move(scr.x() + (scr.width() - ww) // 2, scr.y() + (scr.height() - hh) // 2)


def setup_view(view):
    """Common settings for the big lists (fast + easier to read)."""
    view.setUniformRowHeights(True)
    view.setAlternatingRowColors(True)
    view.setRootIsDecorated(False)


# ---------------------------------------------------------------- signature
def signature_html(c):
    """The signature with 'Danny and Claude' highlighted in the app green."""
    return (f'<span style="color:{c["muted"]}">Engineered by </span>'
            f'<span style="color:{c["accent"]}; font-weight:600">Danny and Claude</span>'
            f'<span style="color:{c["muted"]}"> for you with love</span>')


class SignatureBadge(QWidget):
    """Bottom-right signature: text plus the green tent/campfire logo (same as the app icon)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        c = DARKC if DARK[0] else LIGHT
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 2, 10, 2)
        lay.setSpacing(8)
        self.text_label = QLabel(signature_html(c))
        self.text_label.setToolTip(SIGNATURE)
        f = self.text_label.font()
        f.setItalic(True)
        f.setPointSizeF(max(f.pointSizeF() - 1.5, 7.0))
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102)
        self.text_label.setFont(f)
        self.logo_label = QLabel()
        self.logo_label.setPixmap(ic.logo_pixmap(26, c["accent"], c["base"],
                                                 dpr=self.devicePixelRatioF()))
        lay.addWidget(self.text_label)
        lay.addWidget(self.logo_label)


# ---------------------------------------------------------------- Read Disk
class ReadDiskDialog(QDialog):
    def __init__(self, win, update_node=None, path=None):
        super().__init__(win)
        normal_window(self)
        self.win = win
        self.setWindowTitle("Update Disk" if update_node else "Read Disk")
        self.setMinimumWidth(560)
        f = QFormLayout(self)
        f.setVerticalSpacing(8)
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
        for nid, _nm, gpath in win.groups():
            self.insert.addItem(gpath, nid)
        f.addRow("Insert in:", self.insert)
        self.folders_only = QCheckBox("Read folders only (do not store files)")
        f.addRow("", self.folders_only)
        sup = core.media_support()
        self.archives = QCheckBox("Index archive contents (zip, rar, 7z, tar, iso...)")
        arc_tip = "Lists the files inside archives, so you can search them too."
        if not core.sevenzip_exe():
            arc_tip += "\n7-Zip/PeaZip not found: only zip and tar archives can be read."
        self.archives.setToolTip(arc_tip)
        f.addRow("", self.archives)
        have = [k for k, v in sup.items() if v]
        self.media = QCheckBox("Read media info (music tags, photo EXIF, video length)")
        self.media.setToolTip("Slower on the first read; later updates reuse it for unchanged files."
                              + ("" if len(have) == 3 else
                                 f"\nAvailable now: {', '.join(have) or 'none'} "
                                 "(needs python3-mutagen, python3-pil, ffmpeg)."))
        self.media.setEnabled(bool(have))
        f.addRow("", self.media)
        self.folders_only.toggled.connect(self.sync_options)
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
            r = win.db.execute("SELECT source_path,folders_only,archives,media FROM disks "
                               "WHERE node_id=?", (update_node,)).fetchone()
            if r:
                self.drive.setEditText(path or r[0] or "")
                self.folders_only.setChecked(bool(r[1]))
                self.archives.setChecked(bool(r[2]))
                self.media.setChecked(bool(r[3]) and self.media.isEnabled())
            self.name.setText(self.upd_combo.currentText())
        else:
            if path:
                self.drive.setEditText(path)
            self.autoname()
        self.sync_options()

    def sync_options(self):
        on = not self.folders_only.isChecked()
        self.archives.setEnabled(on)
        self.media.setEnabled(on and any(core.media_support().values()))

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
        normal_window(self)
        self.win = win
        self.ids = node_ids
        multi = len(node_ids) > 1
        rows = [win.db.execute("SELECT name,kind,size,mtime,description,meta FROM nodes WHERE id=?",
                               (n,)).fetchone() for n in node_ids]
        title = rows[0][0] if not multi else f"{len(node_ids)} items"
        self.setWindowTitle(f"Properties for {title}")
        self.resize(480, 420)
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
            loc = QLabel(win.node_path(win.node_row(node_ids[0])[0]))
            loc.setWordWrap(True)
            gl.addRow("Location:", loc)
            if rows[0][5]:
                info = QLabel(rows[0][5])
                info.setWordWrap(True)
                gl.addRow("Info:", info)
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
        normal_window(self)
        self.win = win
        self.disk_id = disk_id
        self.setWindowTitle("Ignored folders/files on this disk")
        self.resize(520, 380)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("These paths are skipped every time you run Update Disk:"))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setSortingEnabled(True)
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
    """Instant search: results appear while you type. Each disk has its own
    background color, so you see at a glance where a file is."""
    HEADER_KEY = "search/header2"
    GEOMETRY_KEY = "search/geometry"

    def __init__(self, win, scope_node):
        super().__init__(None, WINDOW_FLAGS)
        self.win = win
        self.setWindowTitle(f"Search - {APP_NAME}")
        self.setWindowIcon(ic.toolbar_icon("search"))
        self.last_full = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(140)
        self.timer.timeout.connect(lambda: self.run(full=False))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 8)
        lay.setSpacing(8)

        # --- big search field
        self.text = QLineEdit()
        self.text.setObjectName("bigsearch")
        self.text.setClearButtonEnabled(True)
        self.text.setPlaceholderText("Type to search names, descriptions and media info...   "
                                     "(wildcards: * ? #   ·   Enter = show all results)")
        self.text.addAction(ic.toolbar_icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.text.textChanged.connect(lambda _t: self.timer.start())
        self.text.returnPressed.connect(lambda: self.run(full=True))
        lay.addWidget(self.text)

        # --- quick filters
        row = QHBoxLayout()
        row.addWidget(QLabel("In:"))
        self.scope = QComboBox()
        self.scope.addItem("Entire database", None)
        if scope_node is not None:
            self.scope.addItem(win.node_path(scope_node), scope_node)
        self.scope.setMinimumWidth(180)
        row.addWidget(self.scope, 2)
        row.addSpacing(8)
        row.addWidget(QLabel("Type:"))
        self.category = QComboBox()
        self.category.addItem("All types", None)
        self.category.addItem("Folders", "__folders__")
        for cat in core.CATEGORIES:
            self.category.addItem(ic.icon_for_name(KIND_FILE, "x." + core.CATEGORIES[cat].split()[0]),
                                  cat, cat)
        self.category.addItem("Custom extensions...", "__custom__")
        row.addWidget(self.category, 1)
        self.exts = QLineEdit()
        self.exts.setPlaceholderText("e.g. mkv mp4 srt")
        self.exts.setVisible(False)
        self.exts.textChanged.connect(lambda _t: self.timer.start())
        row.addWidget(self.exts, 1)
        row.addSpacing(8)
        row.addWidget(QLabel("Match:"))
        self.mode = QComboBox()
        for lbl, m in (("Phrase", "normal"), ("All words", "all"), ("Any word", "any")):
            self.mode.addItem(lbl, m)
        row.addWidget(self.mode)
        self.more = QToolButton()
        self.more.setText("More options")
        self.more.setCheckable(True)
        self.more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        row.addWidget(self.more)
        lay.addLayout(row)

        # --- more options (hidden until asked for)
        self.opts = QFrame()
        self.opts.setObjectName("card")
        ol = QGridLayout(self.opts)
        ol.setContentsMargins(10, 8, 10, 8)
        self.c_files = QCheckBox("Files")
        self.c_dirs = QCheckBox("Folders")
        self.c_disks = QCheckBox("Disks")
        self.c_desc = QCheckBox("Search in descriptions")
        self.c_meta = QCheckBox("Search in media info")
        self.c_arc = QCheckBox("Inside archives")
        for c in (self.c_files, self.c_dirs, self.c_disks, self.c_desc, self.c_meta, self.c_arc):
            c.setChecked(True)
        self.c_whole = QCheckBox("Match whole name only")
        self.c_case = QCheckBox("Case-sensitive")
        for i, c in enumerate((self.c_files, self.c_dirs, self.c_disks)):
            ol.addWidget(c, i, 0)
        for i, c in enumerate((self.c_desc, self.c_meta, self.c_arc)):
            ol.addWidget(c, i, 1)
        ol.addWidget(self.c_whole, 0, 2)
        ol.addWidget(self.c_case, 1, 2)
        ol.addWidget(QLabel("Size:"), 0, 3)
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
        ol.addWidget(self.size_op, 0, 4)
        ol.addWidget(self.size_val, 0, 5)
        ol.addWidget(self.size_unit, 0, 6)
        self.d_from_on = QCheckBox("Modified after:")
        self.d_to_on = QCheckBox("Modified before:")
        self.d_from = QDateEdit(QDate.currentDate().addYears(-1))
        self.d_to = QDateEdit(QDate.currentDate())
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
        ol.addWidget(self.d_from_on, 1, 3)
        ol.addWidget(self.d_from, 1, 4, 1, 2)
        ol.addWidget(self.d_to_on, 2, 3)
        ol.addWidget(self.d_to, 2, 4, 1, 2)
        ol.setColumnStretch(7, 1)
        self.opts.setVisible(False)
        self.more.toggled.connect(self.opts.setVisible)
        lay.addWidget(self.opts)

        # --- disk legend / filter chips
        self.chips = {}
        chip_box = QWidget()
        self.chip_lay = QHBoxLayout(chip_box)
        self.chip_lay.setContentsMargins(0, 0, 0, 0)
        self.chip_lay.setSpacing(6)
        sa = QScrollArea()
        sa.setWidget(chip_box)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setFixedHeight(34)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(sa)
        self.build_chips()

        # --- results
        self.res = QTreeWidget()
        setup_view(self.res)
        self.res.setSortingEnabled(True)
        self.res.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.res.setHeaderLabels(SEARCH_COLUMNS)
        self.res.itemActivated.connect(lambda it, _c: self.open_item(it))
        self.res.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.res.customContextMenuRequested.connect(self.results_menu)
        lay.addWidget(self.res, 1)
        br = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        br.addWidget(self.status, 1)
        self.export = QPushButton("Export CSV...")
        self.export.clicked.connect(lambda _c=False: self.export_csv())
        br.addWidget(self.export)
        lay.addLayout(br)

        # any filter change re-runs the search
        for w in (self.c_files, self.c_dirs, self.c_disks, self.c_desc, self.c_meta, self.c_arc,
                  self.c_whole, self.c_case, self.d_from_on, self.d_to_on):
            w.toggled.connect(lambda _v: self.timer.start())
        for w in (self.scope, self.mode, self.size_op, self.size_unit):
            w.currentIndexChanged.connect(lambda _i: self.timer.start())
        self.category.currentIndexChanged.connect(self.category_changed)
        self.size_val.valueChanged.connect(lambda _v: self.timer.start())
        self.d_from.dateChanged.connect(lambda _d: self.timer.start())
        self.d_to.dateChanged.connect(lambda _d: self.timer.start())
        QShortcut(QKeySequence("Escape"), self, activated=self.close)
        down = QShortcut(QKeySequence("Down"), self.text, activated=self.focus_results)
        down.setContext(Qt.ShortcutContext.WidgetShortcut)
        dele = QShortcut(QKeySequence("Del"), self.res, activated=self.exclude_selected)
        dele.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.restore_layout()
        self.prefill_from_clipboard()
        self.text.setFocus()

    # ---- disk chips (legend + filter)
    def build_chips(self):
        while self.chip_lay.count():
            w = self.chip_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.chips = {}
        colors = self.win.disk_colors
        lbl = QLabel("Disks:")
        lbl.setObjectName("muted")
        self.chip_lay.addWidget(lbl)
        for did, name in self.win.disk_list():
            b = QToolButton()
            b.setObjectName("chip")
            b.setText(name)
            b.setCheckable(True)
            b.setChecked(True)
            b.setIcon(icon(KIND_DISK, color=colors.get(did)))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setToolTip("Click to include/exclude this disk.\nRight-click: only this disk.")
            col = row_color(colors.get(did)).name()
            txt = (DARKC if DARK[0] else LIGHT)["text"]
            b.setStyleSheet(f"QToolButton#chip {{ background: {col}; color: {txt}; }} "
                            f"QToolButton#chip:!checked {{ background: transparent; color: gray; }}")
            b.toggled.connect(lambda _v: self.timer.start())
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, d=did: self.only_disk(d))
            self.chip_lay.addWidget(b)
            self.chips[did] = b
        self.chip_lay.addStretch(1)

    def only_disk(self, did):
        for d, b in self.chips.items():
            b.blockSignals(True)
            b.setChecked(d == did)
            b.blockSignals(False)
        self.run(full=False)

    def category_changed(self, _i):
        self.exts.setVisible(self.category.currentData() == "__custom__")
        if self.exts.isVisible():
            self.exts.setFocus()
        self.timer.start()

    def focus_text(self):
        self.raise_()
        self.activateWindow()
        self.text.setFocus()
        self.text.selectAll()

    def focus_results(self):
        if self.res.topLevelItemCount():
            self.res.setFocus()
            if not self.res.currentItem():
                self.res.setCurrentItem(self.res.topLevelItem(0))

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
        try:
            st = settings()
            st.setValue(self.HEADER_KEY, self.res.header().saveState())
            st.setValue(self.GEOMETRY_KEY, self.saveGeometry())
        except RuntimeError:  # window already destroyed (program shutting down)
            pass

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
                self.text.blockSignals(True)
                self.text.setText(line[:MAX_PREFILL])
                self.text.blockSignals(False)
                self.text.selectAll()
                return

    def has_filters(self):
        return (self.category.currentData() is not None
                or self.size_op.currentData() is not None
                or self.d_from_on.isChecked() or self.d_to_on.isChecked())

    def run(self, full=True):
        self.timer.stop()
        txt = self.text.text()
        if not txt.strip() and not self.has_filters():
            self.res.clear()
            self.status.setText("Type something to search.")
            return
        kinds = [k for c, ks in ((self.c_files, (KIND_FILE, KIND_ARCHIVE)), (self.c_dirs, (KIND_DIR,)),
                                 (self.c_disks, (KIND_DISK,))) if c.isChecked() for k in ks]
        cat = self.category.currentData()
        exts = None
        if cat == "__folders__":
            kinds, cat = [KIND_DIR], None
        elif cat == "__custom__":
            exts, cat = self.exts.text().replace(",", " ").split(), None
            if not exts:
                cat = None
        date_from = (self.d_from.date().startOfDay().toSecsSinceEpoch()
                     if self.d_from_on.isChecked() else None)
        date_to = (self.d_to.date().endOfDay().toSecsSinceEpoch()
                   if self.d_to_on.isChecked() else None)
        disk_ids = None
        if self.chips and not all(b.isChecked() for b in self.chips.values()):
            disk_ids = [d for d, b in self.chips.items() if b.isChecked()]
        limit = FULL_LIMIT if full else LIVE_LIMIT
        self.last_full = full
        t0 = time.time()
        try:
            rows = core.search(
                self.win.db, txt, mode=self.mode.currentData(), whole=self.c_whole.isChecked(),
                case=self.c_case.isChecked(), in_desc=self.c_desc.isChecked(), kinds=kinds,
                scope_node=self.scope.currentData(), size_op=self.size_op.currentData(),
                size_val=int(self.size_val.value() * self.size_unit.currentData()),
                date_from=date_from, date_to=date_to, limit=limit,
                in_meta=self.c_meta.isChecked(), category=cat, exts=exts,
                in_archives=self.c_arc.isChecked(), disk_ids=disk_ids)
        except sqlite3.OperationalError as ex:
            self.status.setText(f"Search error: {ex}")
            return
        dt = time.time() - t0
        self.fill(rows)
        msg = f"{len(rows)} item(s) in {dt:.3f}s"
        if len(rows) >= limit:
            msg = (f"Showing the first {limit} results - press Enter to show up to {FULL_LIMIT}"
                   if not full else f"{limit} results (limit reached - refine the search)")
        self.status.setText(msg)

    def fill(self, rows):
        colors = self.win.disk_colors
        brushes = {did: QBrush(row_color(c)) for did, c in colors.items()}
        n = len(SEARCH_COLUMNS)
        type_name = self.win.type_name
        self.res.setUpdatesEnabled(False)
        self.res.setSortingEnabled(False)
        self.res.clear()
        items = []
        for nid, kind, name, rel, size, mt, dn, desc, did, meta in rows:
            if kind == KIND_DISK:
                where = ""
            else:
                prel = core.parent_rel(rel)
                where = (dn or "") + ("/" + core.display_rel(prel) if prel else "")
            it = SortItem([name, where, core.fmt_size(size), type_name(kind, name),
                           core.fmt_date(mt), meta or "", desc])
            it.setData(0, ID, nid)
            it.setIcon(0, icon(kind, name, color=colors.get(did) if kind == KIND_DISK else None))
            it.setData(2, KEY, size)
            it.setData(4, KEY, mt or 0)
            it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            b = brushes.get(did)
            if b is not None:
                paint_row(it, b, n)
            items.append(it)
        self.res.addTopLevelItems(items)
        self.res.setSortingEnabled(True)
        self.res.setUpdatesEnabled(True)

    def open_item(self, it):
        self.win.goto(it.data(0, ID))
        self.win.raise_()
        self.win.activateWindow()

    def selected_ids(self):
        return [it.data(0, ID) for it in self.res.selectedItems()]

    def exclude(self, ids):
        """Remove items from the catalog (never from the disk), then refresh the results."""
        if self.win.exclude_nodes(ids, parent=self):
            self.run(full=self.last_full)

    def exclude_selected(self):
        ids = self.selected_ids()
        if ids:
            self.exclude(ids)

    def exclude_menu(self, menu, nid):
        self.win.build_exclude_menu(menu, nid, self.selected_ids(), self.exclude)

    def results_menu(self, pos):
        it = self.res.itemAt(pos)
        if not it:
            return
        nid = it.data(0, ID)
        if not it.isSelected():
            self.res.setCurrentItem(it)
        m = QMenu(self)
        rows_sel = sorted(self.res.selectedItems(), key=self.res.indexOfTopLevelItem)
        files = self.win.openable_ids([x.data(0, ID) for x in rows_sel] or [nid])
        if files:
            m.addAction(ic.toolbar_icon("play"), self.win.open_play_label(files),
                        lambda: self.win.act_open_files(files, self))
        m.addAction(ic.toolbar_icon("open"), "Show in catalog", lambda: self.open_item(it))
        m.addAction(ic.toolbar_icon("open"), "Open file location", lambda: self.win.act_open_location(nid))
        m.addSeparator()
        m.addAction("Copy path to clipboard",
                    lambda: QApplication.clipboard().setText(self.win.node_path(nid)))
        m.addAction("Copy name to clipboard",
                    lambda: QApplication.clipboard().setText(it.text(0)))
        m.addAction(ic.toolbar_icon("properties"), "Properties...",
                    lambda: PropertiesDialog(self.win, [nid]).exec())
        m.addSeparator()
        self.exclude_menu(m, nid)
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


# ---------------------------------------------------------------- Statistics
def _card(value, label):
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 10, 14, 10)
    a = QLabel(value)
    a.setObjectName("cardvalue")
    b = QLabel(label)
    b.setObjectName("cardlabel")
    v.addWidget(a)
    v.addWidget(b)
    return f


class StatsDialog(QDialog):
    def __init__(self, win, scope_node):
        super().__init__(None)  # independent window (not a dialog stuck to the main window)
        normal_window(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.win = win
        self._sorted_once = False
        self.setWindowTitle(f"Statistics - {APP_NAME}")
        self.setWindowIcon(ic.toolbar_icon("stats"))
        fit_to_screen(self, 0.62, 0.80)
        self.lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Statistics for:"))
        self.scope = QComboBox()
        self.scope.addItem("Entire database", None)
        if scope_node is not None:
            self.scope.addItem(win.node_path(scope_node), scope_node)
            self.scope.setCurrentIndex(1)
        self.scope.currentIndexChanged.connect(lambda _i: self.compute())
        top.addWidget(self.scope, 1)
        self.lay.addLayout(top)
        self.cards = QHBoxLayout()
        self.lay.addLayout(self.cards)
        split = QSplitter(Qt.Orientation.Vertical)
        self.cats = QTreeWidget()
        setup_view(self.cats)
        self.cats.setSortingEnabled(True)
        self.cats.setHeaderLabels(["Type", "Files", "Size", "Share of space"])
        self.cats.setColumnWidth(0, 180)
        self.cats.setColumnWidth(1, 100)
        self.cats.setColumnWidth(2, 110)
        split.addWidget(self.cats)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 6, 0, 0)
        self.big_label = QLabel("<b>Largest files</b>")
        bl.addWidget(self.big_label)
        self.big = QTreeWidget()
        setup_view(self.big)
        self.big.setSortingEnabled(True)
        self.big.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.big.setHeaderLabels(["Name", "Size", "Disk", "Folder"])
        self.big.setColumnWidth(0, 330)
        self.big.setColumnWidth(1, 90)
        self.big.setColumnWidth(2, 140)
        self.big.itemDoubleClicked.connect(lambda it, _c: self.win.goto(it.data(0, ID)))
        bl.addWidget(self.big)
        split.addWidget(box)
        split.setSizes([260, 340])
        self.lay.addWidget(split, 1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.close)
        self.lay.addWidget(bb)
        QTimer.singleShot(0, self.compute)

    def compute(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        t0 = time.time()
        try:
            s = core.stats(self.win.db, self.scope.currentData(), top=100, per_disk=50)
        finally:
            QApplication.restoreOverrideCursor()
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cards.addWidget(_card(f"{s['files']:,}".replace(",", " "), "files"))
        self.cards.addWidget(_card(f"{s['folders']:,}".replace(",", " "), "folders"))
        self.cards.addWidget(_card(core.fmt_size(s["size"]), "total size"))
        self.cats.setSortingEnabled(False)
        self.cats.clear()
        total = s["size"] or 1
        for cat, cnt, sz in s["categories"]:
            it = SortItem([cat, f"{cnt:,}".replace(",", " "), core.fmt_size(sz), ""])
            it.setData(1, KEY, cnt)
            it.setData(2, KEY, sz)
            it.setData(3, KEY, sz)
            sample = core.CATEGORIES.get(cat, "").split()
            it.setIcon(0, ic.icon_for_name(KIND_FILE, "x." + sample[0]) if sample
                       else ic.icon_for_name(KIND_FILE, "x"))
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.cats.addTopLevelItem(it)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(sz * 1000 / total))
            bar.setFormat(f"{sz * 100 / total:.1f}%")
            bar.setTextVisible(True)
            bar.setStyleSheet("QProgressBar { max-height: 14px; text-align: center; } "
                              f"QProgressBar::chunk {{ background: {CATEGORY_COLORS.get(cat, '#888')};"
                              " border-radius: 4px; }")
            self.cats.setItemWidget(it, 3, bar)
        self.cats.setSortingEnabled(True)
        if not self._sorted_once:
            self.cats.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        colors = self.win.disk_colors
        self.big.setSortingEnabled(False)
        self.big.clear()
        for nid, nm, sz, dn, rel, did in s["largest"]:
            folder = core.display_rel(core.parent_rel(rel))
            it = SortItem([nm, core.fmt_size(sz), dn or "", folder])
            it.setData(0, ID, nid)
            it.setData(0, KEY, nm.lower())
            it.setData(1, KEY, sz)
            it.setData(2, KEY, (dn or "").lower())
            it.setData(3, KEY, folder.lower())
            it.setData(0, TIE, -(sz or 0))  # same disk/folder: biggest first
            it.setIcon(0, ic.icon_for_name(KIND_FILE, nm))
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if did in colors:
                paint_row(it, QBrush(row_color(colors[did])), 4)
            self.big.addTopLevelItem(it)
        self.big.setSortingEnabled(True)
        if not self._sorted_once:  # default: one disk after the other, biggest first
            self.big.sortByColumn(2, Qt.SortOrder.AscendingOrder)
            self._sorted_once = True
        else:
            h = self.big.header()
            self.big.sortByColumn(h.sortIndicatorSection(), h.sortIndicatorOrder())
        what = (f"the {s['per_disk']} largest of each disk" if s["per_disk"]
                else f"the {len(s['largest'])} largest")
        self.big_label.setText(f"<b>Largest files</b> - {what}  ·  click a column title to sort  ·  "
                               "double-click to show it in the catalog")
        self.status.setText(f"Calculated in {time.time() - t0:.2f}s")


# ---------------------------------------------------------------- Duplicates
class DuplicatesDialog(QWidget):
    """Probable duplicates (same name and same size), across catalogued disks -
    also disks that are not connected. For deep checks (content, similar
    images) a dedicated tool like Czkawka is the right choice."""

    def __init__(self, win):
        super().__init__(None, WINDOW_FLAGS)
        self.win = win
        self.setWindowTitle(f"Find duplicates - {APP_NAME}")
        self.setWindowIcon(ic.toolbar_icon("duplicates"))
        self.resize(1100, 700)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Files of at least"))
        self.min_mb = QDoubleSpinBox()
        self.min_mb.setRange(0, 10**6)
        self.min_mb.setDecimals(1)
        self.min_mb.setValue(float(settings().value("dups/min_mb", 10)))
        self.min_mb.setSuffix(" MB")
        row.addWidget(self.min_mb)
        self.across = QCheckBox("Only copies on different disks")
        self.across.setChecked(settings().value("dups/across", "true") in ("true", True))
        row.addWidget(self.across)
        row.addSpacing(10)
        row.addWidget(QLabel("In:"))
        self.scope = QComboBox()
        self.scope.addItem("Entire database", None)
        for nid, nm in win.disk_list():
            self.scope.addItem(nm, nid)
        row.addWidget(self.scope, 1)
        go = QPushButton("Find")
        go.setObjectName("primary")
        go.clicked.connect(lambda _c=False: self.run())
        row.addWidget(go)
        lay.addLayout(row)
        note = QLabel("Same name and same size = probable duplicate. Works with disks that are "
                      "not connected. For byte-by-byte checks or similar photos, use Czkawka.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(["Name / location", "Size", "Disk / copies", "Modified / extra space"])
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.tree.setColumnWidth(0, 600)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 160)
        self.tree.itemDoubleClicked.connect(self.open_item)
        lay.addWidget(self.tree, 1)
        br = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        br.addWidget(self.status, 1)
        exp = QPushButton("Export CSV...")
        exp.clicked.connect(lambda _c=False: self.export_csv())
        br.addWidget(exp)
        lay.addLayout(br)
        QShortcut(QKeySequence("Escape"), self, activated=self.close)

    def run(self):
        st = settings()
        st.setValue("dups/min_mb", self.min_mb.value())
        st.setValue("dups/across", "true" if self.across.isChecked() else "false")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        t0 = time.time()
        try:
            groups = core.duplicates(self.win.db, int(self.min_mb.value() * 1024 ** 2),
                                     self.across.isChecked(), self.scope.currentData())
        finally:
            QApplication.restoreOverrideCursor()
        colors = self.win.disk_colors
        brushes = {d: QBrush(row_color(c)) for d, c in colors.items()}
        self.tree.setUpdatesEnabled(False)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        wasted = 0
        bold = QFont(self.font())
        bold.setBold(True)
        for nm, sz, items in groups:
            w = sz * (len(items) - 1)
            wasted += w
            top = SortItem([f"{nm}   ×{len(items)}", core.fmt_size(sz), f"{len(items)} copies",
                            f"extra space: {core.fmt_size(w)}"])
            top.setData(0, KEY, nm.lower())
            top.setData(1, KEY, sz)
            top.setData(2, KEY, len(items))
            top.setData(3, KEY, w)
            top.setFont(0, bold)
            top.setIcon(0, ic.icon_for_name(KIND_FILE, nm))
            for nid, did, dn, rel in items:
                mt = self.win.db.execute("SELECT mtime FROM nodes WHERE id=?", (nid,)).fetchone()[0]
                ch = SortItem([f"{dn}/{core.display_rel(rel)}", core.fmt_size(sz), dn or "",
                               core.fmt_date(mt)])
                ch.setData(0, ID, nid)
                ch.setData(1, KEY, sz)
                ch.setData(3, KEY, mt or 0)
                if did in brushes:
                    paint_row(ch, brushes[did], 4)
                top.addChild(ch)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        self.tree.setSortingEnabled(True)
        h = self.tree.header()
        self.tree.sortByColumn(h.sortIndicatorSection(), h.sortIndicatorOrder())
        self.tree.setUpdatesEnabled(True)
        self.status.setText(f"{len(groups)} group(s) of probable duplicates · "
                            f"{core.fmt_size(wasted)} could be freed · {time.time() - t0:.2f}s")

    def open_item(self, it, _col=0):
        nid = it.data(0, ID)
        if nid is not None:
            self.win.goto(nid)
            self.win.raise_()
            self.win.activateWindow()

    def export_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export duplicates", "duplicates.csv", "CSV (*.csv)")
        if not p:
            return
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Group", "Path", "Size", "Disk", "Modified"])
            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                for j in range(top.childCount()):
                    ch = top.child(j)
                    w.writerow([i + 1, ch.text(0), ch.text(1), ch.text(2), ch.text(3)])


# ---------------------------------------------------------------- Main window
class MainWindow(QMainWindow):
    def __init__(self, db_path):
        super().__init__()
        geo = settings().value("main/geometry")
        if geo is None or not self.restoreGeometry(geo):
            fit_to_screen(self, 0.90, 0.88)
        self.hist, self.hpos = [], -1
        self.searches = []
        self.dups_window = None
        self.scanning = False      # a disk is being read right now
        self.stop_scan = False     # the user asked to close the program during a read
        self.close_after_scan = False
        self.current = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.db_path = None
        self.disk_colors = {}
        self.open_path(db_path, initial=True)
        self.build_ui()
        self.load_root()
        # watch for catalogued disks being connected
        self.known_mounts = core.mount_points()
        self.mount_timer = QTimer(self)
        self.mount_timer.setInterval(3000)
        self.mount_timer.timeout.connect(self.check_mounts)
        self.mount_timer.start()

    # ---- database / undo / save
    def open_path(self, path, initial=False):
        self.db = core.load_into_memory(path) if path else core.new_memory_db()
        self.db_path = path
        if path:
            settings().setValue("last_db", path)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.reload_colors()
        if not initial:
            self.update_title()
            self.update_undo_actions()

    def reload_colors(self):
        self.disk_colors = core.disk_colors(self.db)

    def db_title(self):
        if not self.db_path:
            return "Untitled"
        return os.path.splitext(os.path.basename(self.db_path))[0]

    def update_title(self):
        star = " *" if self.dirty else ""
        where = self.db_path or "(new, unsaved)"
        self.setWindowTitle(f"{APP_NAME} - {where}{star}")

    def mark_dirty(self):
        self.dirty = True
        self.update_title()

    def push_undo(self):
        """Call BEFORE any change to the database."""
        self.undo_stack.append(core.snapshot(self.db))
        self.redo_stack.clear()
        self.trim_undo()
        self.update_undo_actions()

    def trim_undo(self):
        """At most UNDO_LIMIT steps and about UNDO_MAX_BYTES of memory (oldest dropped first)."""
        while len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
        while (len(self.undo_stack) > 1 and
               sum(map(len, self.undo_stack)) + sum(map(len, self.redo_stack)) > UNDO_MAX_BYTES):
            self.undo_stack.pop(0)

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
        self.reload_colors()
        self.load_root()
        if cur is not None and self.node_row(cur):
            self.select_tree(cur)
        for s in self.searches:
            s.build_chips()
            if s.res.topLevelItemCount():
                s.run(full=s.last_full)

    def update_undo_actions(self):
        if hasattr(self, "a_undo"):
            self.a_undo.setEnabled(bool(self.undo_stack))
            self.a_redo.setEnabled(bool(self.redo_stack))

    def maybe_save_prompt(self):
        """True to continue (saved or discarded), False to cancel."""
        if not self.dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(APP_NAME)
        box.setText("The catalog has unsaved changes.")
        box.setInformativeText("Save them, discard them, or go back?")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        r = box.exec()
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            return self.act_save()
        return True  # Discard

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
                parts.append(core.display_rel(rel))
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
        if kind == KIND_ARCHIVE:
            return f"{ext} Archive" if ext else "Archive"
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

    def toolbar_button(self, action, extra_actions, label=None):
        """Toolbar button that runs `action` on click and offers `extra_actions`
        in a small drop-down. The action itself carries no menu, so it keeps
        working as a normal File/Edit menu entry and keeps its shortcut."""
        btn = QToolButton()
        btn.setDefaultAction(action)
        btn.setAutoRaise(True)
        btn.setObjectName("split")
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        if label:
            btn.setText(label)
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
        tm = mb.addMenu("&Tools")
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
        self.a_search = act("Search...", self.act_search, ["Ctrl+F", "F3"], "search", em, app_wide=True)
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
        vm.addSeparator()
        self.a_notify = QAction("Notify when a catalogued disk is connected", self)
        self.a_notify.setCheckable(True)
        self.a_notify.setChecked(settings().value("notify_mounts", "true") in ("true", True))
        self.a_notify.toggled.connect(
            lambda v: settings().setValue("notify_mounts", "true" if v else "false"))
        vm.addAction(self.a_notify)

        self.a_stats = act("Statistics...", self.act_stats, "Ctrl+I", "stats", tm)
        self.a_dups = act("Find duplicates...", self.act_dups, "Ctrl+Shift+D", "duplicates", tm)
        tm.addSeparator()
        act("Disk color...", self.act_disk_color, None, None, tm)
        act("About", self.act_about, None, None, hm)

        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(tb)
        short = {self.a_open: "Open", self.a_save: "Save", self.a_read: "Add Disk",
                 self.a_mkdir: "New Folder", self.a_export_list: "Export", self.a_update: "Update",
                 self.a_del: "Delete", self.a_search: "Search", self.a_stats: "Statistics",
                 self.a_dups: "Duplicates", self.a_undo: "Undo", self.a_redo: "Redo",
                 self.a_back: "Back", self.a_fwd: "Forward", self.a_up: "Up"}

        def add(a):
            tb.addAction(a)
            w = tb.widgetForAction(a)
            if isinstance(w, QToolButton) and a in short:
                w.setText(short[a])
                w.setToolTip(a.text().replace("...", "") + (
                    f"  ({a.shortcut().toString()})" if not a.shortcut().isEmpty() else ""))

        add(self.a_back)
        add(self.a_fwd)
        add(self.a_up)
        tb.addSeparator()
        add(self.a_open)
        add(self.a_save)
        add(self.a_export_list)
        tb.addSeparator()
        add(self.a_read)
        add(self.a_mkdir)
        add(self.a_update)
        add(self.a_del)
        tb.addSeparator()
        add(self.a_search)
        add(self.a_stats)
        add(self.a_dups)
        tb.addSeparator()
        add(self.a_undo)
        add(self.a_redo)

        # banner shown when a catalogued disk gets connected
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 6, 6)
        self.banner_icon = QLabel()
        self.banner_icon.setPixmap(ic.toolbar_icon("refresh").pixmap(22, 22))
        bl.addWidget(self.banner_icon)
        self.banner_text = QLabel("")
        bl.addWidget(self.banner_text, 1)
        self.banner_update = QToolButton()
        self.banner_update.setText("Update now")
        self.banner_update.setObjectName("chip")
        bl.addWidget(self.banner_update)
        dismiss = QToolButton()
        dismiss.setText("Not now")
        dismiss.clicked.connect(lambda _c=False: self.banner.hide())
        bl.addWidget(dismiss)
        self.banner.hide()

        self.addr = QLineEdit()
        self.addr.setObjectName("address")
        self.addr.setReadOnly(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self.tree_expand)
        self.tree.currentItemChanged.connect(self.tree_select)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.lst = QTreeWidget()
        setup_view(self.lst)
        self.lst.setSortingEnabled(True)
        self.lst.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.lst.setHeaderLabels(["Name", "Size", "Type", "Modified", "Info", "Description"])
        self.lst.setColumnWidth(0, 400)
        self.lst.setColumnWidth(1, 90)
        self.lst.setColumnWidth(2, 120)
        self.lst.setColumnWidth(3, 140)
        self.lst.setColumnWidth(4, 260)
        self.lst.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.lst.itemActivated.connect(lambda it, _c: self.goto(it.data(0, ID)))
        self.lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lst.customContextMenuRequested.connect(self.list_menu)
        self.lst.itemSelectionChanged.connect(self.update_status)
        sp_ = QSplitter()
        sp_.setHandleWidth(8)
        sp_.addWidget(self.tree)
        sp_.addWidget(self.lst)
        sp_.setStretchFactor(1, 1)
        sp_.setSizes([300, 1000])
        c = QWidget()
        v = QVBoxLayout(c)
        v.setContentsMargins(8, 8, 8, 4)
        v.setSpacing(6)
        v.addWidget(self.banner)
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
        normal_window(dlg)
        dlg.setWindowTitle(f"About {APP_NAME} for Linux")
        lay = QVBoxLayout(dlg)
        ink, paper = theme_colors(dlg)
        logo = QLabel()
        logo.setPixmap(ic.logo_pixmap(120, (DARKC if DARK[0] else LIGHT)["accent"], paper,
                                      dpr=dlg.devicePixelRatioF()))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(logo)
        info = QLabel(f"<b>{APP_NAME} for Linux</b> &nbsp;v{APP_VERSION}<br>"
                      "Offline catalog of external and local disks.<br>"
                      "Database: SQLite (.dcat)<br><br>"
                      f"Author: {APP_AUTHOR} &nbsp;·&nbsp; License: {APP_LICENSE}<br>"
                      f'<a href="{APP_HOMEPAGE}">{APP_HOMEPAGE}</a>')
        info.setOpenExternalLinks(True)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(info)
        sig = QLabel(signature_html(DARKC if DARK[0] else LIGHT))
        f = sig.font()
        f.setItalic(True)
        sig.setFont(f)
        sig.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sig)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    # ---- connected-disk detection
    def check_mounts(self):
        now = core.mount_points()
        new = now - self.known_mounts
        self.known_mounts = now
        if not new or not self.a_notify.isChecked():
            return
        found = []
        for mp in sorted(new):
            try:
                found += core.catalogued_disks_on_mount(self.db, mp)
            except Exception:  # noqa: BLE001
                continue
        if not found:
            return
        names = ", ".join(f"\u201c{nm}\u201d" for _d, nm, _p in found[:3])
        more = f" and {len(found) - 3} more" if len(found) > 3 else ""
        self.banner_text.setText(f"<b>{names}{more}</b> is connected. Update its catalog?"
                                 if len(found) == 1 else
                                 f"<b>{names}{more}</b> are connected. Update their catalogs?")
        try:
            self.banner_update.clicked.disconnect()
        except TypeError:
            pass
        self.banner_update.setMenu(None)
        if len(found) == 1:
            did, _nm, path = found[0]
            self.banner_update.clicked.connect(lambda _c=False: self.banner_go(did, path))
            self.banner_update.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        else:
            m = QMenu(self.banner_update)
            for did, nm, path in found:
                m.addAction(nm, lambda d=did, p=path: self.banner_go(d, p))
            self.banner_update.setMenu(m)
            self.banner_update.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.banner.show()

    def banner_go(self, did, path):
        self.banner.hide()
        self.read_dialog(did, path)

    # ---- tree
    def make_tree_item(self, nid, name, kind, remote=False, has_sub=True):
        it = QTreeWidgetItem([name])
        it.setData(0, ID, nid)
        it.setIcon(0, icon(kind, name, remote, self.disk_colors.get(nid) if kind == KIND_DISK else None))
        if has_sub:
            it.addChild(QTreeWidgetItem(["..."]))
        return it

    def has_subfolders(self, nid):
        return self.db.execute("SELECT 1 FROM nodes WHERE parent_id IS ? AND kind IN (1,2,3,4) LIMIT 1",
                               (nid,)).fetchone() is not None

    def fill_children(self, item):
        nid = item.data(0, ID)
        item.takeChildren()
        for cid, nm, kind, rem in self.db.execute(
                "SELECT n.id,n.name,n.kind,COALESCE(d.remote,0) FROM nodes n LEFT JOIN disks d "
                "ON d.node_id=n.id WHERE n.parent_id IS ? AND n.kind IN (1,2,3,4) "
                "ORDER BY CASE n.kind WHEN 3 THEN 0 WHEN 2 THEN 1 WHEN 1 THEN 2 ELSE 3 END, n.name_lc",
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
            if kind in (KIND_DISK, KIND_DIR, KIND_ARCHIVE):
                m.addAction(ic.toolbar_icon("open"), "Open file location",
                            lambda: self.act_open_location(nid))
            m.addAction(ic.toolbar_icon("search"), "Search here...", lambda: self.act_search(nid))
            m.addAction(ic.toolbar_icon("stats"), "Statistics...", lambda: self.act_stats(nid))
            if kind == KIND_DISK:
                m.addSeparator()
                m.addAction(ic.toolbar_icon("refresh"), "Update Disk...", lambda: self.act_update(nid))
                m.addAction("Disk color...", lambda: self.act_disk_color(nid))
                m.addAction("Ignored folders...", lambda: self.act_manage_ignored(nid))
            if nid is not None:
                m.addSeparator()
                self.build_exclude_menu(m, nid, [], self.exclude_nodes)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def delete_nodes(self, ids):
        """Delete catalog disks/folders given directly by id (used by the tree
        menu, where there is no selection in the central list)."""
        self.exclude_nodes(ids)

    def build_exclude_menu(self, menu, nid, selected, do_exclude):
        """'Exclude from catalog' sub-menu: the selection, the item itself and
        every folder above it, up to its disk."""
        sub = menu.addMenu(ic.toolbar_icon("delete"), "Exclude from catalog")
        if len(selected) > 1:
            sub.addAction(f"Exclude the {len(selected)} selected items", lambda: do_exclude(selected))
            sub.addSeparator()
        x = nid
        while x is not None:
            r = self.node_row(x)
            if not r:
                break
            pid, kind, name, _did = r
            if kind == KIND_GROUP:
                sub.addAction(icon(kind, name), f"Exclude catalog folder \u201c{name}\u201d",
                              lambda i=x: do_exclude([i]))
                break
            label = {KIND_FILE: "file", KIND_DIR: "folder", KIND_ARCHIVE: "archive",
                     KIND_DISK: "disk"}.get(kind, "item")
            if kind == KIND_DISK:
                sub.addSeparator()
            sub.addAction(icon(kind, name, color=self.disk_colors.get(x) if kind == KIND_DISK else None),
                          f"Exclude {label} \u201c{name}\u201d", lambda i=x: do_exclude([i]))
            if kind == KIND_DISK:
                break
            x = pid
        return sub

    def exclude_nodes(self, ids, parent=None):
        """Remove items from the CATALOG (files on the disks are never touched).
        Files/folders are also remembered, so Update Disk skips them later.
        Asks for confirmation; Ctrl+Z undoes it. Returns True when done."""
        rows = []
        for nid in dict.fromkeys(ids):
            r = self.node_row(nid)
            if r:
                rows.append((nid, r[1], r[2]))
        disks = [(n, nm) for n, k, nm in rows if k == KIND_DISK]
        groups = [(n, nm) for n, k, nm in rows if k == KIND_GROUP]
        inner = [(n, nm) for n, k, nm in rows if k in (KIND_DIR, KIND_FILE, KIND_ARCHIVE)]
        if not rows:
            return False

        def names(lst):
            out = [f"   \u2022 {nm}" for _, nm in lst[:12]]
            if len(lst) > 12:
                out.append(f"   ... and {len(lst) - 12} more")
            return "\n".join(out)
        parts = []
        if disks:
            parts.append("WHOLE DISK(S) - everything catalogued on them is removed:\n" + names(disks))
        if groups:
            parts.append("Catalog folder(s), with the disks inside them:\n" + names(groups))
        if inner:
            parts.append("Removed from the catalog and skipped on future updates of the disk:\n"
                         + names(inner))
        box = QMessageBox(parent or self)
        box.setIcon(QMessageBox.Icon.Warning if (disks or groups) else QMessageBox.Icon.Question)
        box.setWindowTitle("Exclude from catalog")
        box.setText("Exclude from the catalog?\n\n" + "\n\n".join(parts) +
                    "\n\nFiles on the disks are not touched. You can undo with Ctrl+Z.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Exclude")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel if (disks or groups)
                             else QMessageBox.StandardButton.Yes)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return False
        self.push_undo()
        for nid, _ in disks:
            core.delete_disk(self.db, nid)
        for nid, _ in groups:
            if self.node_row(nid):
                core.delete_group(self.db, nid)
        for nid, _ in inner:
            core.delete_and_ignore(self.db, nid)  # silently skips items already removed
        self.mark_dirty()
        self.refresh()
        return True

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
        colors = self.disk_colors
        items = []
        for cid, kind, nm, size, mt, ds, rem, meta in self.db.execute(
                "SELECT n.id,n.kind,n.name,n.size,n.mtime,n.description,COALESCE(d.remote,0),n.meta "
                "FROM nodes n LEFT JOIN disks d ON d.node_id=n.id WHERE n.parent_id IS ?", (nid,)):
            tname = ("Network Drive" if rem else "Local Disk") if kind == KIND_DISK else self.type_name(kind, nm)
            it = SortItem([nm, core.fmt_size(size) if kind != KIND_GROUP else "", tname,
                           core.fmt_date(mt), meta or "", ds])
            it.setData(0, ID, cid)
            it.setIcon(0, icon(kind, nm, bool(rem), colors.get(cid) if kind == KIND_DISK else None))
            it.setData(0, KEY, (0 if kind not in core.FILE_KINDS else 1, nm.lower()))
            it.setData(1, KEY, size)
            it.setData(3, KEY, mt or 0)
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if kind == KIND_DISK and cid in colors:
                paint_row(it, QBrush(row_color(colors[cid])), 6)
            items.append(it)
            if select == cid:
                sel_item = it
        self.lst.addTopLevelItems(items)
        self.lst.setSortingEnabled(True)
        self.lst.setUpdatesEnabled(True)
        if sel_item:
            self.lst.setCurrentItem(sel_item)
            self.lst.scrollToItem(sel_item)
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
            rows_sel = sorted(self.lst.selectedItems(), key=self.lst.indexOfTopLevelItem)
            files = self.openable_ids([x.data(0, ID) for x in rows_sel] or [nid])
            if files:
                m.addAction(ic.toolbar_icon("play"), self.open_play_label(files),
                            lambda: self.act_open_files(files))
            if kind != KIND_FILE:
                m.addAction(ic.toolbar_icon("open"), "Open", lambda: self.goto(nid))
            if kind != KIND_GROUP:
                m.addAction(ic.toolbar_icon("open"), "Open file location",
                            lambda: self.act_open_location(nid))
            m.addSeparator()
            m.addAction(ic.toolbar_icon("search"), "Search...", self.act_search)
            if kind != KIND_FILE:
                m.addAction(ic.toolbar_icon("stats"), "Statistics...", lambda: self.act_stats(nid))
            m.addSeparator()
            m.addAction("Copy path to clipboard", lambda: QApplication.clipboard().setText(
                self.node_path(nid)))
            m.addAction(ic.toolbar_icon("properties"), "Properties...", self.act_properties)
            if kind == KIND_DISK:
                m.addAction(ic.toolbar_icon("refresh"), "Update Disk...", lambda: self.act_update(nid))
                m.addAction("Disk color...", lambda: self.act_disk_color(nid))
                m.addAction("Ignored folders...", lambda: self.act_manage_ignored(nid))
            m.addSeparator()
            sel = [x.data(0, ID) for x in self.lst.selectedItems()]
            self.build_exclude_menu(m, nid, sel, self.exclude_nodes)
        else:
            m.addAction(ic.toolbar_icon("add_disk"), "Add Disk/Folder...", self.act_read)
            m.addAction(ic.toolbar_icon("new_folder"), "New Folder", self.act_mkfolder)
        m.exec(self.lst.viewport().mapToGlobal(pos))

    def openable_ids(self, ids):
        """Only files and archives (folders/disks are left out), without repeats."""
        out = []
        for i in dict.fromkeys(ids):
            r = self.node_row(i)
            if r and r[1] in (KIND_FILE, KIND_ARCHIVE):
                out.append(i)
        return out

    def open_play_label(self, ids):
        n = len(ids)
        if n > 1:
            return f"Open / Play the {n} selected files"
        inside = core.ARC_SEP in (self.db.execute(
            "SELECT relpath FROM nodes WHERE id=?", (ids[0],)).fetchone()[0] or "")
        return "Open archive" if inside else "Open / Play"

    def act_open_file(self, nid, parent=None):
        """Open / play one file with the default application."""
        self.act_open_files([nid], parent)

    def act_open_files(self, ids, parent=None):
        """Open / play several files with their default applications: songs go to
        the music player together (as a playlist), PDFs open together, etc."""
        parent = parent or self
        paths, missing = [], {}
        for nid in ids:
            path, disk = core.resolve_real_path(self.db, nid)
            if path:
                if path not in paths:  # items inside the same archive open it once
                    paths.append(path)
            else:
                missing[disk] = missing.get(disk, 0) + 1
        if paths:
            ok, msg = core.open_many_with_default_apps(paths)
            if not ok:
                QMessageBox.warning(parent, "Open / Play", msg)
            else:
                self.statusBar().showMessage(
                    f"Opening {os.path.basename(paths[0])} ..." if len(paths) == 1
                    else f"Opening {len(paths)} files ...", 4000)
        if missing:
            disks = ", ".join(f"\u201c{d}\u201d" for d in missing)
            n = sum(missing.values())
            QMessageBox.information(parent, "Open / Play",
                                    f"{n} file(s) could not be opened: the disk {disks} is not "
                                    "connected (or the files are no longer there).\n\n"
                                    "Connect the disk and try again.")

    def act_open_location(self, nid=None):
        """Show the item in the file manager (Nemo), selected when possible."""
        if type(nid) is not int:
            sel = self.lst.selectedItems()
            nid = sel[0].data(0, ID) if sel else self.current
        if nid is None or core.node_real_path(self.db, nid) is None:
            QMessageBox.information(self, "Open file location",
                                    "This item has no location on disk (it is a catalog folder).")
            return
        path, disk = core.resolve_real_path(self.db, nid)
        if not path:
            QMessageBox.information(self, "Open file location",
                                    f"The disk \u201c{disk}\u201d is not connected (or the item is no "
                                    "longer there).\n\nConnect the disk and try again.")
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

    def selected_disk(self):
        """Disk of the current folder, else the selected disk in the list."""
        if self.current is not None:
            d = self.node_row(self.current)[3]
            if d:
                return d
        sel = self.lst.selectedItems()
        if sel and self.node_row(sel[0].data(0, ID))[1] == KIND_DISK:
            return sel[0].data(0, ID)
        return None

    def act_update(self, nid=None):
        if type(nid) is not int:
            nid = self.selected_disk()
        self.read_dialog(nid)

    def read_dialog(self, update_node, path=None):
        dlg = ReadDiskDialog(self, update_node, path)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path = dlg.path()
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Read Disk", f"Folder not found:\n{path}")
            return
        upd = dlg.upd_combo.currentData() if dlg.upd.isChecked() else None
        self.push_undo()
        scan_errors = []
        pd = QProgressDialog("Starting...", "Cancel", 0, 0, self)
        pd.setWindowFlags(WINDOW_FLAGS)
        pd.setWindowTitle("Update Disk" if upd else "Read Disk")
        pd.setWindowModality(Qt.WindowModality.WindowModal)
        pd.setMinimumDuration(0)
        pd.setMinimumWidth(600)
        pd.setAutoClose(False)
        pd.setAutoReset(False)
        bar = QProgressBar()
        bar.setTextVisible(True)
        bar.setFormat("%p%")
        bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c = DARKC if DARK[0] else LIGHT
        bar.setStyleSheet(f"QProgressBar {{ max-height: 22px; min-height: 22px; border-radius: 6px; "
                          f"background: {c['head']}; color: {c['text']}; font-weight: 600; }} "
                          f"QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}")
        pd.setBar(bar)
        pd.show()
        t_start = time.monotonic()
        self.scanning, self.stop_scan, self.close_after_scan = True, False, False

        def progress(n, p, frac=None):
            elapsed = time.monotonic() - t_start
            if frac is None:
                if pd.maximum() != 0:
                    pd.setRange(0, 0)  # unknown total: moving bar
                pd.setLabelText(f"{n:,} items read  ·  {fmt_elapsed(elapsed)} elapsed\n"
                                f"(no percentage: first read of a folder)\n{p[-90:]}")
            else:
                if pd.maximum() != 1000:
                    pd.setRange(0, 1000)
                pd.setValue(int(frac * 1000))
                left = ""
                if frac > 0.01 and elapsed > 3:
                    rest = fmt_left(elapsed * (1 - frac) / frac)
                    left = f"  ·  {rest} left" if rest.startswith("less") else f"  ·  about {rest} left"
                pd.setLabelText(f"{frac * 100:.0f}% done  ·  {n:,} items{left}\n"
                                f"{fmt_elapsed(elapsed)} elapsed\n{p[-90:]}")
            QApplication.processEvents()

        try:
            did = core.scan_disk(self.db, path, dlg.name.text().strip() or "disk",
                                 parent_id=dlg.insert.currentData(), update_node=upd,
                                 folders_only=dlg.folders_only.isChecked(),
                                 progress=progress,
                                 cancel=lambda: pd.wasCanceled() or self.stop_scan,
                                 errors=scan_errors,
                                 archives=dlg.archives.isChecked() and dlg.archives.isEnabled(),
                                 media=dlg.media.isChecked() and dlg.media.isEnabled())
            if dlg.desc.text().strip():
                self.db.execute("UPDATE nodes SET description=?,desc_lc=? WHERE id=?",
                                (dlg.desc.text(), dlg.desc.text().lower(), did))
        except core.Cancelled:
            pd.close()
            self.scanning = False
            self.undo_stack.pop()  # nothing relevant changed; keep the history clean
            self.update_undo_actions()
            self.statusBar().showMessage("Cancelled - the catalog was not changed.", 6000)
            if self.close_after_scan:
                QTimer.singleShot(0, self.close)
                return
            QMessageBox.information(
                self, "Update Disk" if upd else "Read Disk",
                "Reading was cancelled.\n\nNothing was changed: the catalog is exactly as it "
                "was before" + (" this update" if upd else "") + ". Run it again whenever you like.")
            return
        except Exception as ex:  # noqa: BLE001
            pd.close()
            self.scanning = False
            self.undo_stack.pop()
            self.update_undo_actions()
            QMessageBox.critical(self, "Read Disk", str(ex))
            return
        pd.close()
        self.scanning = False
        self.mark_dirty()
        self.refresh()
        self.select_tree(did)
        self.show_scan_errors(scan_errors)

    def show_scan_errors(self, errors):
        """Report items that could not be read; the scan itself has finished."""
        if not errors:
            return
        unreadable = [e for e in errors if e[2] is not None]
        archives = [e for e in errors if e[2] is None]
        msgs = []
        if unreadable:
            kept = sum(1 for e in unreadable if e[2])
            msgs.append(f"{len(unreadable)} item(s) could not be read.\n"
                        f"For {kept} of them the previously catalogued content was kept.")
        if archives:
            msgs.append(f"{len(archives)} archive(s) could not be opened (password-protected, "
                        "damaged or unsupported). They are catalogued as normal files.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if unreadable else QMessageBox.Icon.Information)
        box.setWindowTitle("Read Disk")
        box.setText("Finished.\n\n" + "\n\n".join(msgs))
        lines = [f"{p}\n    {m}" + ("   [no previous data]" if k is False else "")
                 for p, m, k in errors[:500]]
        if len(errors) > 500:
            lines.append(f"... and {len(errors) - 500} more")
        box.setDetailedText("\n".join(lines))
        box.exec()

    def act_delete(self):
        ids = [it.data(0, ID) for it in self.lst.selectedItems()]
        if ids:
            self.exclude_nodes(ids)

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

    def act_disk_color(self, nid=None):
        if type(nid) is not int:
            nid = self.selected_disk()
        if nid is None:
            QMessageBox.information(self, "Disk color", "Select a disk first.")
            return
        cur = QColor(self.disk_colors.get(nid, "#ffffff"))
        col = QColorDialog.getColor(cur, self, f"Color for \u201c{self.node_row(nid)[2]}\u201d")
        if not col.isValid():
            return
        self.push_undo()
        core.set_disk_color(self.db, nid, col.name())
        self.mark_dirty()
        self.refresh()

    def act_search(self, scope=None):
        """Ctrl+F: bring up the search window (reusing an open one), ready to type."""
        if self.searches:
            s = self.searches[-1]
            s.show()
            s.focus_text()
            return
        if type(scope) is not int:
            scope = self.current
            sel = self.lst.selectedItems()
            if sel and self.node_row(sel[0].data(0, ID))[1] != KIND_FILE:
                scope = sel[0].data(0, ID)
        d = SearchDialog(self, scope)
        self.searches.append(d)
        d.show()
        d.focus_text()
        if d.text.text():
            d.run(full=False)

    def act_stats(self, scope=None):
        if type(scope) is not int:
            scope = self.current
        d = StatsDialog(self, scope)  # its own window: maximize it, keep it open next to the catalog
        self.stats_windows = [w for w in getattr(self, "stats_windows", []) if w.isVisible()] + [d]
        d.show()

    def act_dups(self):
        if self.dups_window is None:
            self.dups_window = DuplicatesDialog(self)
        self.dups_window.show()
        self.dups_window.raise_()
        self.dups_window.activateWindow()

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
            w.writerow(["Name", "Size", "Type", "Modified", "Info", "Description"])
            for i in range(self.lst.topLevelItemCount()):
                it = self.lst.topLevelItem(i)
                w.writerow([it.text(c) for c in range(6)])

    def refresh(self):
        cur = self.current
        self.reload_colors()
        self.load_root()
        if cur is not None and self.node_row(cur):
            self.select_tree(cur)
        for s in self.searches:
            s.build_chips()

    def closeEvent(self, e):
        if self.scanning:
            r = QMessageBox.question(
                self, APP_NAME, "A disk is still being read.\n\nStop reading and close?\n"
                "(The catalog stays as it was before this read.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r == QMessageBox.StandardButton.Yes:
                self.stop_scan = True
                self.close_after_scan = True
            e.ignore()
            return
        if not self.maybe_save_prompt():
            self.stop_scan = self.close_after_scan = False
            e.ignore()
            return
        settings().setValue("main/geometry", self.saveGeometry())  # size/position for next time
        for s in list(self.searches):
            s.close()
        if self.dups_window is not None:
            self.dups_window.close()
        super().closeEvent(e)


def default_catalog_path():
    """Where the catalog is stored when nothing else is known (the last catalog
    used is remembered in the settings and opened first)."""
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
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("tent-cataloguer")
    apply_theme(app)
    app.setWindowIcon(QIcon(ic.logo_pixmap(64, "#111111", "#ffffff", dpr=1.0)))
    path = startup_catalog_path(sys.argv)
    try:
        w = MainWindow(path)
    except sqlite3.DatabaseError as ex:
        QMessageBox.critical(None, APP_NAME, f"Cannot open the catalog:\n{path}\n\n{ex}")
        sys.exit(1)
    install_crash_guard(lambda: w)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
