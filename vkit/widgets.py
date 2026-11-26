"""Rulers, colour palette and tool icons."""
import colorsys
import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .model import PX_PER_MM


# ------------------------------------------------------------------ rulers
class Ruler(QWidget):
    def __init__(self, view, horizontal):
        super().__init__()
        self.v = view
        self.h = horizontal
        self.pos_px = None
        self.drag_idx = None
        if horizontal:
            self.setFixedHeight(20)
        else:
            self.setFixedWidth(20)
        self.setCursor(Qt.SplitVCursor if horizontal else Qt.SplitHCursor)
        view.horizontalScrollBar().valueChanged.connect(self.update)
        view.verticalScrollBar().valueChanged.connect(self.update)
        view.zoomChanged.connect(self.update)
        view.cursorMoved.connect(self.on_cursor)

    def on_cursor(self, p):
        self.pos_px = p.x() if self.h else p.y()
        self.update()

    def _step(self, z):
        mm_px = PX_PER_MM * z
        for st in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
            if st * mm_px >= 48:
                return st
        return 1000

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ECEFEE"))
        p.setPen(QColor("#A099A5"))
        z = self.v.zoom_factor()
        step = self._step(z)
        sub = 10 if step in (1, 10, 100, 1000) else 5
        f = QFont(); f.setPixelSize(9); p.setFont(f)
        if self.h:
            a, b = self.v.mapToScene(0, 0).x(), self.v.mapToScene(self.v.viewport().width(), 0).x()
        else:
            a, b = self.v.mapToScene(0, 0).y(), self.v.mapToScene(0, self.v.viewport().height()).y()
        a_mm, b_mm = a / PX_PER_MM, b / PX_PER_MM
        k = math.floor(a_mm / step) * step
        minor = step / sub
        n = 0
        while k <= b_mm + step and n < 4000:
            for j in range(sub):
                mm = k + j * minor
                sp = mm * PX_PER_MM
                if self.h:
                    x = self.v.mapFromScene(QPointF(sp, 0)).x()
                    ln = 20 if j == 0 else (11 if j * 2 == sub else 6)
                    p.drawLine(x, 20 - ln, x, 20)
                    if j == 0:
                        p.setPen(QColor("#3A3240")); p.drawText(x + 2, 9, f"{mm:g}"); p.setPen(QColor("#A099A5"))
                else:
                    y = self.v.mapFromScene(QPointF(0, sp)).y()
                    ln = 20 if j == 0 else (11 if j * 2 == sub else 6)
                    p.drawLine(20 - ln, y, 20, y)
                    if j == 0:
                        p.save(); p.translate(9, y - 2); p.rotate(-90); p.setPen(QColor("#3A3240")); p.drawText(0, 0, f"{mm:g}"); p.restore()
            k += step
            n += 1
        if self.pos_px is not None:
            p.setPen(QPen(QColor(255, 0, 140), 1))
            if self.h:
                x = self.v.mapFromScene(QPointF(self.pos_px, 0)).x(); p.drawLine(x, 0, x, 20)
            else:
                y = self.v.mapFromScene(QPointF(0, self.pos_px)).y(); p.drawLine(0, y, 20, y)
        p.setPen(QColor("#BDB7C2"))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1) if self.h else p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

    # drag from a ruler to make a guide: top ruler -> horizontal guide, left ruler -> vertical guide
    def _scene_pt(self, ev):
        g = ev.globalPosition().toPoint()
        return self.v.mapToScene(self.v.viewport().mapFromGlobal(g)), self.v.viewport().rect().contains(self.v.viewport().mapFromGlobal(g))

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        key = "h" if self.h else "v"
        pt, _ = self._scene_pt(ev)
        self.v.guides[key].append(pt.y() if self.h else pt.x())
        self.drag_idx = len(self.v.guides[key]) - 1
        self.v.viewport().update()

    def mouseMoveEvent(self, ev):
        if self.drag_idx is None:
            return
        key = "h" if self.h else "v"
        pt, _ = self._scene_pt(ev)
        self.v.guides[key][self.drag_idx] = pt.y() if self.h else pt.x()
        self.v.viewport().update()

    def mouseReleaseEvent(self, ev):
        if self.drag_idx is None:
            return
        key = "h" if self.h else "v"
        _, inside = self._scene_pt(ev)
        if not inside:
            self.v.guides[key].pop(self.drag_idx)
        else:
            self.v.commit("Add guide")
        self.drag_idx = None
        self.v.viewport().update()


# ------------------------------------------------------------------ colour palette
def build_palette():
    cols = ["#000000", "#222222", "#444444", "#666666", "#888888", "#AAAAAA", "#CCCCCC", "#E6E6E6", "#FFFFFF"]
    light = [(1.0, 0.45), (0.95, 0.75), (0.85, 0.95), (0.6, 1.0), (0.35, 1.0)]    # (saturation, value) variants dark -> light
    for h in range(0, 360, 20):
        for s, vv in ((1.0, 0.55), (1.0, 0.85), (1.0, 1.0), (0.6, 1.0), (0.3, 1.0)):
            r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, vv)
            cols.append("#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255)))
    cols[9:9] = ["#C2395C", "#FF6F8E", "#4A1F6E"]
    return cols


class Palette(QWidget):
    pickFill = Signal(object)         # color string or None for "no colour"
    pickStroke = Signal(object)
    ROWS = 5
    SZ = 16

    def __init__(self):
        super().__init__()
        self.colors = build_palette()
        self.fillc = "#C2395C"
        self.strokec = "#1B1040"
        self.setFixedHeight(self.ROWS * self.SZ + 6)
        self.setMinimumWidth(300)
        self.setToolTip("Left click: fill colour. Right click: outline colour.")

    def set_current(self, fill, stroke):
        self.fillc, self.strokec = fill, stroke
        self.update()

    def cell(self, i):
        col, row = divmod(i, self.ROWS)
        return QRectF(70 + col * self.SZ, 3 + row * self.SZ, self.SZ - 1, self.SZ - 1)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ECEFEE"))
        # current fill / outline indicator
        p.setPen(QPen(QColor("#555"), 1))
        p.setBrush(QColor(self.strokec) if self.strokec not in (None, "none") else Qt.NoBrush)
        p.drawRect(QRectF(26, 26, 32, 32))
        p.setBrush(QColor("#ECEFEE")); p.drawRect(QRectF(34, 34, 16, 16))
        p.setBrush(QColor(self.fillc) if self.fillc not in (None, "none") else Qt.NoBrush)
        p.drawRect(QRectF(6, 6, 34, 34))
        if self.fillc in (None, "none"):
            p.setPen(QPen(QColor("red"), 1)); p.drawLine(6, 40, 40, 6)
        # "no colour" cell, then swatches
        p.setPen(QPen(QColor("#888"), 1)); p.setBrush(QColor("white"))
        p.drawRect(QRectF(50, 3, self.SZ - 1, self.SZ - 1))
        p.setPen(QPen(QColor("red"), 1)); p.drawLine(50, 3 + self.SZ - 1, 50 + self.SZ - 1, 3)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for i, c in enumerate(self.colors):
            p.setBrush(QColor(c))
            p.drawRect(self.cell(i))

    def hit(self, pos):
        if QRectF(50, 3, self.SZ - 1, self.SZ - 1).contains(pos):
            return None, True
        for i, c in enumerate(self.colors):
            if self.cell(i).contains(pos):
                return c, True
        return None, False

    def mousePressEvent(self, ev):
        c, ok = self.hit(ev.position())
        if not ok:
            return
        if ev.button() == Qt.LeftButton:
            self.pickFill.emit(c)
        elif ev.button() == Qt.RightButton:
            self.pickStroke.emit(c)


# ------------------------------------------------------------------ tool icons (drawn, so no image files are needed)
def tool_icon(name):
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    ink = QColor("#1E2B25")
    green = QColor("#C2395C")
    pen = QPen(ink, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if name == "select":
        poly = QPolygonF([QPointF(9, 5), QPointF(9, 24), QPointF(14, 19.5), QPointF(17.5, 27), QPointF(20.5, 25.7), QPointF(17, 18.5), QPointF(23.5, 18.5)])
        p.setBrush(QColor("white")); p.drawPolygon(poly)
    elif name == "node":
        path = QPainterPath(QPointF(5, 25)); path.cubicTo(10, 4, 22, 4, 27, 20)
        p.setPen(QPen(green, 2)); p.drawPath(path)
        p.setPen(pen); p.setBrush(QColor("white"))
        for pt in (QPointF(5, 25), QPointF(27, 20)):
            p.drawRect(QRectF(pt.x() - 3, pt.y() - 3, 6, 6))
        p.setBrush(green); p.drawEllipse(QPointF(16, 9.5), 3, 3)
    elif name == "pen":
        poly = QPolygonF([QPointF(16, 3), QPointF(24, 14), QPointF(19, 22), QPointF(13, 22), QPointF(8, 14)])
        p.setBrush(QColor("white")); p.drawPolygon(poly)
        p.drawLine(16, 22, 16, 29); p.setBrush(green); p.drawEllipse(QPointF(16, 14), 2.2, 2.2)
    elif name == "rect":
        p.setBrush(QColor("#DBC6EB")); p.drawRoundedRect(QRectF(5, 8, 22, 16), 2, 2)
    elif name == "ellipse":
        p.setBrush(QColor("#DBC6EB")); p.drawEllipse(QRectF(4, 7, 24, 18))
    elif name == "polygon":
        pts = [QPointF(16 + 11 * math.cos(-math.pi / 2 + i * math.pi / 3), 16 + 11 * math.sin(-math.pi / 2 + i * math.pi / 3)) for i in range(6)]
        p.setBrush(QColor("#DBC6EB")); p.drawPolygon(QPolygonF(pts))
    elif name == "star":
        pts = []
        for i in range(10):
            r = 12 if i % 2 == 0 else 5.2
            a = -math.pi / 2 + i * math.pi / 5
            pts.append(QPointF(16 + r * math.cos(a), 16.5 + r * math.sin(a)))
        p.setBrush(QColor("#DBC6EB")); p.drawPolygon(QPolygonF(pts))
    elif name == "line":
        p.drawLine(6, 26, 26, 6)
        p.setBrush(QColor("white")); p.drawEllipse(QPointF(6, 26), 2.5, 2.5); p.drawEllipse(QPointF(26, 6), 2.5, 2.5)
    elif name == "pencil":
        path = QPainterPath(QPointF(4, 25)); path.cubicTo(8, 14, 12, 28, 16, 17); path.cubicTo(18, 12, 24, 14, 28, 8)
        p.setPen(QPen(green, 2.2, Qt.SolidLine, Qt.RoundCap)); p.drawPath(path)
    elif name == "text":
        f = QFont("Sans Serif"); f.setBold(True); f.setPixelSize(26)
        p.setFont(f); p.setPen(ink); p.drawText(QRectF(0, 0, 32, 32), Qt.AlignCenter, "T")
    elif name == "eyedropper":
        p.drawLine(8, 24, 18, 14)
        p.setBrush(QColor("#FFFFFF")); p.drawPolygon(QPolygonF([QPointF(5, 27), QPointF(8, 20), QPointF(12, 24)]))
        p.save(); p.translate(21, 11); p.rotate(45); p.setBrush(green); p.drawRoundedRect(QRectF(-3, -8, 6, 16), 2, 2); p.restore()
    elif name == "artboard":
        p.setBrush(QColor("white")); p.drawRect(QRectF(9, 9, 14, 14))
        for a, b in (((9, 3), (9, 7)), ((23, 25), (23, 29)), ((3, 9), (7, 9)), ((25, 23), (29, 23))):
            p.drawLine(QPointF(*a), QPointF(*b))
        p.setPen(QPen(green, 1.6)); p.drawLine(QPointF(9, 3), QPointF(9, 29)); p.drawLine(QPointF(3, 23), QPointF(29, 23))
    elif name == "zoom":
        p.setBrush(QColor("white")); p.drawEllipse(QPointF(13, 13), 8, 8)
        p.setPen(QPen(ink, 3, Qt.SolidLine, Qt.RoundCap)); p.drawLine(19, 19, 27, 27)
    elif name == "hand":
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(9, 13, 14, 12), 4, 4)
        for x, top in ((10.5, 7), (14, 5), (17.5, 5), (21, 8)):
            p.drawRoundedRect(QRectF(x, top, 3, 14), 1.5, 1.5)
    p.end()
    return QIcon(pm)
