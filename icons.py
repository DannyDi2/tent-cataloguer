"""icons.py - icons drawn from scratch with QPainter (no image files, no
third-party assets). Flat style with colors inspired by the Windows 10
file explorer, but with original geometry - they are not copies of any
existing icon set.
"""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

S = 32  # base canvas size


def _canvas():
    pm = QPixmap(S, S)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def _icon(draw_fn):
    pm, p = _canvas()
    draw_fn(p)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- basics
def _folder_path(x, y, w, h, tab=0.42):
    path = QPainterPath()
    tabw = w * tab
    path.moveTo(x, y + h * 0.18)
    path.lineTo(x + tabw * 0.5, y + h * 0.18)
    path.lineTo(x + tabw, y)
    path.lineTo(x + w * 0.42, y)
    path.lineTo(x + w * 0.5, y + h * 0.18)
    path.lineTo(x + w, y + h * 0.18)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    return path


def draw_folder(p, color="#f5c542", open_=False):
    x, y, w, h = 3, 8, 26, 19
    if open_:
        body = QPainterPath()
        body.moveTo(x, y + 2)
        body.lineTo(x + w, y + 2)
        body.lineTo(x + w - 2, y + h)
        body.lineTo(x - 2, y + h)
        body.closeSubpath()
        p.setBrush(QBrush(QColor(color).darker(115)))
        p.setPen(QPen(QColor(color).darker(160), 1))
        back = _folder_path(x - 1, y - 4, w, 9)
        p.drawPath(back)
        p.setBrush(QBrush(QColor(color)))
        p.drawPath(body)
    else:
        back = _folder_path(x, y - 2, w, h + 2)
        p.setBrush(QBrush(QColor(color)))
        p.setPen(QPen(QColor(color).darker(150), 1))
        p.drawPath(back)


def draw_file(p, color="#e8eef4", ext_color="#5b7fa6", tag=""):
    x, y, w, h = 8, 3, 16, 26
    fold = 6
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w - fold, y)
    path.lineTo(x + w, y + fold)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.setBrush(QBrush(QColor(color)))
    p.setPen(QPen(QColor("#9aa7b4"), 1))
    p.drawPath(path)
    ear = QPainterPath()
    ear.moveTo(x + w - fold, y)
    ear.lineTo(x + w - fold, y + fold)
    ear.lineTo(x + w, y + fold)
    ear.closeSubpath()
    p.setBrush(QBrush(QColor(color).darker(115)))
    p.drawPath(ear)
    if tag:
        p.setBrush(QBrush(QColor(ext_color)))
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(x - 1, y + h - 9, w + 2, 9)
        p.drawRoundedRect(r, 2, 2)
        f = QFont("Sans", 6, QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QPen(QColor("white")))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, tag)


def draw_disk(p, color="#4f86c6", remote=False, network_only=False):
    base = QColor(color)
    p.setPen(QPen(base.darker(160), 1))
    p.setBrush(QBrush(base))
    p.drawRoundedRect(QRectF(2, 9, 28, 17), 3, 3)
    p.setBrush(QBrush(base.lighter(135)))
    p.drawRoundedRect(QRectF(2, 9, 28, 6), 3, 3)
    p.setPen(QPen(base.darker(160), 1))
    p.drawLine(QPointF(5, 20.5), QPointF(17, 20.5))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawEllipse(QRectF(22.5, 18.5, 4.5, 4.5))
    if remote or network_only:
        p.setPen(QPen(QColor("#2e7d32"), 2.5))
        p.drawLine(QPointF(16, 2), QPointF(16, 9))
        p.drawLine(QPointF(10.5, 5.5), QPointF(16, 2))
        p.drawLine(QPointF(21.5, 5.5), QPointF(16, 2))


def draw_catalog_group(p, color="#e0a640"):
    draw_folder(p, color)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.setPen(QPen(QColor("#8a5a00"), 1))
    p.drawRect(QRectF(11, 13, 10, 8))
    p.drawLine(QPointF(11, 16), QPointF(21, 16))


EXT_COLORS = {
    "mkv": ("#c0392b", "VID"), "mp4": ("#c0392b", "VID"), "avi": ("#c0392b", "VID"),
    "mov": ("#c0392b", "VID"), "webm": ("#c0392b", "VID"),
    "mp3": ("#8e44ad", "AUD"), "flac": ("#8e44ad", "AUD"), "wav": ("#8e44ad", "AUD"),
    "ogg": ("#8e44ad", "AUD"),
    "jpg": ("#16a085", "IMG"), "jpeg": ("#16a085", "IMG"), "png": ("#16a085", "IMG"),
    "gif": ("#16a085", "IMG"), "bmp": ("#16a085", "IMG"), "webp": ("#16a085", "IMG"),
    "zip": ("#d35400", "ZIP"), "rar": ("#d35400", "RAR"), "7z": ("#d35400", "7Z"),
    "tar": ("#d35400", "TAR"), "gz": ("#d35400", "GZ"),
    "pdf": ("#b71c1c", "PDF"), "doc": ("#2b579a", "DOC"), "docx": ("#2b579a", "DOC"),
    "xls": ("#217346", "XLS"), "xlsx": ("#217346", "XLS"),
    "txt": ("#607d8b", "TXT"), "iso": ("#455a64", "ISO"),
}


def icon_for_name(kind, name):
    from dcat_core import KIND_DIR, KIND_DISK, KIND_GROUP

    def draw(p):
        if kind == KIND_GROUP:
            draw_catalog_group(p)
        elif kind == KIND_DISK:
            draw_disk(p)
        elif kind == KIND_DIR:
            draw_folder(p)
        else:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            color, tag = EXT_COLORS.get(ext, (None, ext[:3].upper()))
            if color:
                draw_file(p, tag=tag, ext_color=color)
            else:
                draw_file(p, tag=tag if tag else "")
    return _icon(draw)


def icon_disk_remote():
    return _icon(lambda p: draw_disk(p, remote=True))


def icon_folder_open():
    return _icon(lambda p: draw_folder(p, open_=True))


# ---------------------------------------------------------- toolbar
def _pen(c="#37474f", w=2):
    return QPen(QColor(c), w, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)


def draw_open(p):
    draw_folder(p, "#f2b632", open_=True)


def draw_save(p):
    x, y, w, h = 5, 4, 22, 24
    p.setBrush(QBrush(QColor("#3b6fb5")))
    p.setPen(QPen(QColor("#2a5590"), 1))
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w - 5, y)
    path.lineTo(x + w, y + 5)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QBrush(QColor("#eef3fa")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(QRectF(x + 4, y + 2, w - 10, 8))
    p.setBrush(QBrush(QColor("white")))
    p.drawRect(QRectF(x + 4, y + h - 9, w - 8, 8))
    p.setBrush(QBrush(QColor("#2a5590")))
    p.drawRect(QRectF(x + w - 9, y + h - 8, 4, 6))


def draw_print(p):
    p.setBrush(QBrush(QColor("#90a4ae")))
    p.setPen(QPen(QColor("#546e7a"), 1))
    p.drawRect(QRectF(5, 11, 22, 12))
    p.setBrush(QBrush(QColor("white")))
    p.drawRect(QRectF(9, 4, 14, 10))
    p.drawRect(QRectF(9, 21, 14, 8))
    p.setPen(QPen(QColor("#78909c")))
    for yy in (24, 26.5):
        p.drawLine(QPointF(11, yy), QPointF(19, yy))
    p.setBrush(QBrush(QColor("#2ecc71")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(21, 14, 4, 4))


def draw_add_disk(p):
    draw_disk(p, "#7fa8d9")
    p.setBrush(QBrush(QColor("#2e7d32")))
    p.setPen(QPen(QColor("white"), 1))
    p.drawEllipse(QRectF(19, 2, 12, 12))
    p.setPen(QPen(QColor("white"), 2))
    p.drawLine(QPointF(25, 5), QPointF(25, 11))
    p.drawLine(QPointF(22, 8), QPointF(28, 8))


def draw_refresh(p):
    pen = _pen("#2e7d32", 3)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(6, 6, 20, 20)
    p.drawArc(rect, 40 * 16, 260 * 16)
    p.drawArc(rect, 220 * 16, 260 * 16)
    p.setBrush(QBrush(QColor("#2e7d32")))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPainterPath()
    tri.moveTo(24, 4)
    tri.lineTo(29, 9)
    tri.lineTo(21, 10)
    tri.closeSubpath()
    p.drawPath(tri)
    tri2 = QPainterPath()
    tri2.moveTo(8, 28)
    tri2.lineTo(3, 23)
    tri2.lineTo(11, 22)
    tri2.closeSubpath()
    p.drawPath(tri2)


def draw_delete(p):
    p.setPen(_pen("#c0392b", 4))
    p.drawLine(QPointF(8, 8), QPointF(24, 24))
    p.drawLine(QPointF(24, 8), QPointF(8, 24))


def draw_search(p):
    p.setPen(_pen("#37474f", 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(5, 5, 15, 15))
    p.drawLine(QPointF(17, 17), QPointF(26, 26))


def draw_undo(p):
    p.setPen(_pen("#37474f", 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(7, 7, 18, 18)
    p.drawArc(rect, 20 * 16, 300 * 16)
    p.setBrush(QBrush(QColor("#37474f")))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPainterPath()
    tri.moveTo(9, 4)
    tri.lineTo(9, 14)
    tri.lineTo(1, 9)
    tri.closeSubpath()
    p.drawPath(tri)


def draw_redo(p):
    p.setPen(_pen("#37474f", 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(7, 7, 18, 18)
    p.drawArc(rect, -20 * 16, -300 * 16)
    p.setBrush(QBrush(QColor("#37474f")))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPainterPath()
    tri.moveTo(23, 4)
    tri.lineTo(23, 14)
    tri.lineTo(31, 9)
    tri.closeSubpath()
    p.drawPath(tri)


def draw_new_folder(p):
    draw_folder(p, "#f5c542")
    p.setBrush(QBrush(QColor("#2e7d32")))
    p.setPen(QPen(QColor("white"), 1))
    p.drawEllipse(QRectF(18, 15, 12, 12))
    p.setPen(QPen(QColor("white"), 2))
    p.drawLine(QPointF(24, 18), QPointF(24, 24))
    p.drawLine(QPointF(21, 21), QPointF(27, 21))


def draw_properties(p):
    p.setBrush(QBrush(QColor("#eceff1")))
    p.setPen(QPen(QColor("#90a4ae"), 1))
    p.drawRoundedRect(QRectF(4, 4, 24, 24), 3, 3)
    p.setPen(QPen(QColor("#546e7a"), 2))
    for i, yy in enumerate((10, 16, 22)):
        p.drawLine(QPointF(8, yy), QPointF(24 if i else 20, yy))


def draw_back(p):
    p.setBrush(QBrush(QColor("#37474f")))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPainterPath()
    tri.moveTo(20, 5)
    tri.lineTo(20, 27)
    tri.lineTo(6, 16)
    tri.closeSubpath()
    p.drawPath(tri)


def draw_forward(p):
    p.setBrush(QBrush(QColor("#37474f")))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPainterPath()
    tri.moveTo(12, 5)
    tri.lineTo(12, 27)
    tri.lineTo(26, 16)
    tri.closeSubpath()
    p.drawPath(tri)


def draw_up(p):
    p.setBrush(QBrush(QColor("#37474f")))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPainterPath()
    tri.moveTo(16, 6)
    tri.lineTo(27, 20)
    tri.lineTo(5, 20)
    tri.closeSubpath()
    p.drawPath(tri)


def draw_dropdown_arrow(p):
    p.setBrush(QBrush(QColor("#37474f")))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPainterPath()
    tri.moveTo(9, 12)
    tri.lineTo(23, 12)
    tri.lineTo(16, 20)
    tri.closeSubpath()
    p.drawPath(tri)


_TOOLBAR = {
    "open": draw_open, "save": draw_save, "print": draw_print, "add_disk": draw_add_disk,
    "refresh": draw_refresh, "delete": draw_delete, "search": draw_search,
    "undo": draw_undo, "redo": draw_redo, "new_folder": draw_new_folder,
    "properties": draw_properties, "back": draw_back, "forward": draw_forward, "up": draw_up,
    "dropdown": draw_dropdown_arrow,
}


def toolbar_icon(name):
    return _icon(_TOOLBAR[name])


# ------------------------------------------------------------------- logo
def _flame(path_scale=1.0):
    """Teardrop flame centred on x=32, tip at y=27*, base at y=42.5."""
    p = QPainterPath()
    p.moveTo(32, 26)
    p.cubicTo(34.5, 31, 39, 33, 37.6, 38)
    p.cubicTo(37, 41.2, 34.6, 42.8, 32, 42.8)
    p.cubicTo(29.4, 42.8, 27, 41.2, 26.4, 38)
    p.cubicTo(25.8, 35, 29, 33.5, 29.2, 30.2)
    p.cubicTo(30.2, 29.2, 31.4, 28, 32, 26)
    p.closeSubpath()
    return p


def draw_logo(p, fg, bg):
    """Black-and-white campfire inside a tent (canvas 64 x 48).
    `fg` is the ink color, `bg` the paper color (flame highlights, tent door)."""
    fg, bg = QColor(fg), QColor(bg)
    # ground line
    p.setPen(QPen(fg, 2, cap=Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(3, 45), QPointF(61, 45))
    # tent body
    body = QPainterPath()
    body.moveTo(32, 7)
    body.lineTo(59, 44)
    body.lineTo(5, 44)
    body.closeSubpath()
    p.setPen(QPen(fg, 1.5, join=Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(fg))
    p.drawPath(body)
    # crossed pole tips at the apex
    p.setPen(QPen(fg, 2, cap=Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(32, 8), QPointF(26.5, 1.5))
    p.drawLine(QPointF(32, 8), QPointF(37.5, 1.5))
    # tent door (paper colored) with the two folded-back flaps
    door = QPainterPath()
    door.moveTo(32, 15)
    door.lineTo(48.5, 44)
    door.lineTo(15.5, 44)
    door.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawPath(door)
    # campfire: logs, flame, inner flame, sparks (all ink on the paper door)
    p.setPen(QPen(fg, 2.4, cap=Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(24.5, 43.5), QPointF(39.5, 39.8))
    p.drawLine(QPointF(39.5, 43.5), QPointF(24.5, 39.8))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(fg))
    p.drawPath(_flame())
    inner = QPainterPath()
    inner.moveTo(32, 33.5)
    inner.cubicTo(33.6, 36, 35, 37, 34.4, 39.2)
    inner.cubicTo(34, 40.6, 33, 41.2, 32, 41.2)
    inner.cubicTo(31, 41.2, 30, 40.6, 29.6, 39.2)
    inner.cubicTo(29, 37.3, 31.2, 36.2, 32, 33.5)
    inner.closeSubpath()
    p.setBrush(QBrush(bg))
    p.drawPath(inner)
    p.setBrush(QBrush(fg))
    for (x, y, r) in ((29.4, 24.2, 1.0), (35.0, 21.6, 0.9)):
        p.drawEllipse(QPointF(x, y), r, r)


def logo_pixmap(height, fg="#000000", bg="#ffffff", dpr=2.0):
    """Logo as a QPixmap `height` logical pixels tall (aspect 64:48)."""
    w = round(height * 64 / 48)
    pm = QPixmap(round(w * dpr), round(height * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(w / 64.0, height / 48.0)
    draw_logo(p, fg, bg)
    p.end()
    return pm
