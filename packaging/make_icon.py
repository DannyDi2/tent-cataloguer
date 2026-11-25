"""Renders the application icon (PNG) at the given sizes: green tent + campfire
on a rounded white tile. Usage: make_icon.py OUTDIR"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QGuiApplication, QPainter, QPen, QPixmap
import icons as ic

app = QGuiApplication(sys.argv)
out = sys.argv[1]
for size in (48, 64, 128, 256):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m = size * 0.04
    p.setPen(QPen(QColor("#cfd8dc"), max(1, size / 96)))
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), size * 0.2, size * 0.2)
    w, h = size * 0.78, size * 0.78 * 48 / 64
    p.translate((size - w) / 2, (size - h) / 2 + size * 0.02)
    p.scale(w / 64.0, h / 48.0)
    ic.draw_logo(p, "#1f8a63", "#ffffff")
    p.end()
    d = os.path.join(out, f"{size}x{size}", "apps")
    os.makedirs(d, exist_ok=True)
    pm.save(os.path.join(d, "tent-cataloguer.png"))
print("icons ok")
