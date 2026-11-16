"""The drawing area: page, grid, guides, snapping, zoom/pan, undo snapshots and the active tool."""
import json
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPen, QTransform
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from .model import (DEFAULT_LAYERS, PX_PER_MM, Page, VGroup, VItem, dump_scene, geom_rect, item_from_json, layers_from, load_into,
                    top_level, union_rect)


class VectorView(QGraphicsView):
    selectionChanged2 = Signal()
    docChanged = Signal()
    layersChanged = Signal()
    message = Signal(str)
    zoomChanged = Signal(float)
    cursorMoved = Signal(QPointF)
    toolRequest = Signal(str)

    def __init__(self):
        super().__init__()
        self.scene_ = QGraphicsScene(self)
        self.scene_.setSceneRect(-20000, -20000, 40000, 40000)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#4D4950")))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.page = Page()
        self.guides = {"h": [], "v": []}
        self.layers = [dict(l) for l in DEFAULT_LAYERS]
        self.cur_layer = "L1"
        self.wireframe = False
        self.grid = {"show": False, "snap": False, "mm": 5.0}
        self.snap_objects = True
        self.snap_guides = True
        self.show_guides = True
        self.fill = {"type": "solid", "c1": "#C2395C", "c2": "#FFFFFF", "angle": 0}
        self.stroke = {"color": "#1B1040", "width": 2.0, "dash": "solid", "join": "miter", "cap": "flat"}
        self.opt = {"radius": 0.0, "sides": 6, "points": 5, "inner": 0.5}
        self.tool = None
        self.tools = {}
        self._pan = None
        self._space = False
        self.undo_stack, self.redo_stack = [], []
        self.dirty = False
        self.path = None
        self.scene_.selectionChanged.connect(self._sel_changed)
        self._snap_marker = None
        self.commit_base()

    # ------------------------------------------------------------ tools
    def register(self, name, tool):
        self.tools[name] = tool

    def set_tool(self, name):
        if self.tool is not None:
            self.tool.deactivate()
        self.tool = self.tools[name]
        self.tool_name = name
        self.setCursor(self.tool.cursor)
        self.tool.activate()
        self.viewport().update()

    # ------------------------------------------------------------ document
    def selected(self):
        return [i for i in self.scene_.selectedItems() if i.group() is None]

    def snapshot(self):
        return json.dumps(dump_scene(self.scene_, self.page, self.guides, self.layers))

    def commit_base(self):
        self._base = self.snapshot()

    # ------------------------------------------------------------ layers
    def layer_index(self, lid):
        for k, l in enumerate(self.layers):
            if l["id"] == lid:
                return k
        return -1

    def new_layer_id(self):
        n = 1
        ids = {l["id"] for l in self.layers}
        while f"L{n}" in ids:
            n += 1
        return f"L{n}"

    def normalize_z(self):
        """stacking = layer order first (last layer on top), then the order the objects already had"""
        items = top_level(self.scene_)
        for k, it in enumerate(items):
            if it.layer is None or self.layer_index(it.layer) < 0:
                it.layer = self.cur_layer if self.layer_index(self.cur_layer) >= 0 else self.layers[0]["id"]
                it.setZValue(1e9 + k)                       # new objects start on top of their layer
        items = sorted(items, key=lambda i: (self.layer_index(i.layer), i.zValue()))
        for n, it in enumerate(items):
            it.setZValue(n)

    def apply_layers(self):
        for it in top_level(self.scene_):
            k = self.layer_index(it.layer)
            lay = self.layers[k] if k >= 0 else {"visible": True, "locked": False}
            it.setVisible(not it.hidden and lay["visible"])
            it.layer_locked = bool(lay["locked"])
            if it.is_locked() and it.isSelected():
                it.setSelected(False)

    def commit(self, label="Change"):
        """call after finishing an edit"""
        self.normalize_z()
        self.apply_layers()
        snap = self.snapshot()
        if snap != self._base:
            self.undo_stack.append((self._base, label))
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
            self._base = snap
            self.dirty = True
        self.docChanged.emit()
        self.viewport().update()

    def restore(self, snap):
        sel_idx = [top_level(self.scene_).index(i) for i in self.selected() if i in top_level(self.scene_)]
        self.scene_.blockSignals(True)
        data = json.loads(snap)
        self.page, self.guides = load_into(self.scene_, data)
        self.layers = layers_from(data)
        if self.layer_index(self.cur_layer) < 0:
            self.cur_layer = self.layers[0]["id"]
        self.apply_layers()
        self.layersChanged.emit()
        self.scene_.blockSignals(False)
        tl = top_level(self.scene_)
        for k in sel_idx:
            if k < len(tl):
                tl[k].setSelected(True)
        self._base = snap
        self.dirty = True
        self.docChanged.emit()
        self.selectionChanged2.emit()
        self.viewport().update()

    def undo(self):
        if self.undo_stack:
            prev, label = self.undo_stack.pop()
            self.redo_stack.append((self.snapshot(), label))
            self.restore(prev)
            self.message.emit(f"Undo: {label}")

    def redo(self):
        if self.redo_stack:
            nxt, label = self.redo_stack.pop()
            self.undo_stack.append((self.snapshot(), label))
            self.restore(nxt)
            self.message.emit(f"Redo: {label}")

    def new_document(self, w_mm=210.0, h_mm=297.0):
        for i in top_level(self.scene_):
            self.scene_.removeItem(i)
        self.page = Page(w_mm, h_mm)
        self.guides = {"h": [], "v": []}
        self.layers = [dict(l) for l in DEFAULT_LAYERS]
        self.cur_layer = "L1"
        self.layersChanged.emit()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.commit_base()
        self.dirty = False
        self.path = None
        self.fit_page()
        self.docChanged.emit()

    def _sel_changed(self):
        self.selectionChanged2.emit()
        self.viewport().update()

    def add_item(self, it, label="Draw"):
        self.scene_.addItem(it)
        self.scene_.clearSelection()
        it.setSelected(True)
        self.commit(label)

    # ------------------------------------------------------------ view
    def fit_page(self):
        r = self.page.rect.adjusted(-40, -40, 40, 40)
        self.fitInView(r, Qt.KeepAspectRatio)
        self.zoomChanged.emit(self.zoom_factor())

    def zoom_factor(self):
        return self.transform().m11()

    def zoom_to(self, z, anchor=None):
        z = max(0.02, min(64.0, z))
        cur = self.zoom_factor()
        self.scale(z / cur, z / cur)
        self.zoomChanged.emit(self.zoom_factor())
        self.viewport().update()

    def zoom_rect(self, r):
        if r.width() > 2 and r.height() > 2:
            self.fitInView(r, Qt.KeepAspectRatio)
            self.zoomChanged.emit(self.zoom_factor())

    def zoom_selection(self):
        s = self.selected()
        if s:
            self.zoom_rect(union_rect(s).adjusted(-20, -20, 20, 20))

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ShiftModifier:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - e.angleDelta().y())
            return
        if e.modifiers() & Qt.AltModifier:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - e.angleDelta().y())
            return
        f = 1.15 ** (e.angleDelta().y() / 120.0)
        self.zoom_to(self.zoom_factor() * f)

    def mapPos(self, e):
        return self.mapToScene(e.position().toPoint())

    # ------------------------------------------------------------ snapping
    def snap_pt(self, p, exclude=(), force=False):
        tol = 8.0 / self.zoom_factor()
        best, bd = QPointF(p), tol
        marks = []
        cx = cy = None
        if self.grid["snap"] or force:
            g = self.grid["mm"] * PX_PER_MM
            gx, gy = round(p.x() / g) * g, round(p.y() / g) * g
            if abs(gx - p.x()) < tol:
                cx = gx
            if abs(gy - p.y()) < tol:
                cy = gy
        xs, ys = [], []
        if self.snap_guides:
            xs += self.guides["v"]; ys += self.guides["h"]
        pr = self.page.rect
        xs += [pr.left(), pr.center().x(), pr.right()]
        ys += [pr.top(), pr.center().y(), pr.bottom()]
        if self.snap_objects:
            for it in top_level(self.scene_):
                if it in exclude or not it.isVisible():
                    continue
                r = geom_rect(it)
                xs += [r.left(), r.center().x(), r.right()]
                ys += [r.top(), r.center().y(), r.bottom()]
        bx = min(xs, key=lambda v: abs(v - p.x()), default=None)
        by = min(ys, key=lambda v: abs(v - p.y()), default=None)
        if bx is not None and abs(bx - p.x()) < tol and (cx is None or abs(bx - p.x()) <= abs(cx - p.x())):
            cx = bx
        if by is not None and abs(by - p.y()) < tol and (cy is None or abs(by - p.y()) <= abs(cy - p.y())):
            cy = by
        out = QPointF(cx if cx is not None else p.x(), cy if cy is not None else p.y())
        self._snap_marker = (cx, cy)
        return out

    # ------------------------------------------------------------ drawing
    def drawBackground(self, p, rect):
        super().drawBackground(p, rect)
        pr = self.page.rect
        p.fillRect(pr.translated(5 / self.zoom_factor(), 5 / self.zoom_factor()), QColor(0, 0, 0, 70))
        p.fillRect(pr, QColor("#FFFFFF"))
        if self.grid["show"]:
            g = self.grid["mm"] * PX_PER_MM
            if g * self.zoom_factor() >= 5:
                p.setPen(QPen(QColor(0, 0, 0, 40), 0))
                x = math.ceil(pr.left() / g) * g
                while x <= pr.right():
                    p.drawLine(QPointF(x, pr.top()), QPointF(x, pr.bottom())); x += g
                y = math.ceil(pr.top() / g) * g
                while y <= pr.bottom():
                    p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y)); y += g
        p.setPen(QPen(QColor(0, 0, 0, 90), 0))
        p.setBrush(Qt.NoBrush)
        p.drawRect(pr)

    def drawForeground(self, p, rect):
        z = self.zoom_factor()
        if self.show_guides:
            p.setPen(QPen(QColor(0, 160, 255, 200), 0, Qt.DashLine))
            for y in self.guides["h"]:
                p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            for x in self.guides["v"]:
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if self._snap_marker and self.tool is not None and getattr(self.tool, "dragging", False):
            cx, cy = self._snap_marker
            p.setPen(QPen(QColor(255, 0, 140, 220), 0))
            if cx is not None:
                p.drawLine(QPointF(cx, rect.top()), QPointF(cx, rect.bottom()))
            if cy is not None:
                p.drawLine(QPointF(rect.left(), cy), QPointF(rect.right(), cy))
        if self.tool is not None:
            self.tool.paint(p, z)

    # ------------------------------------------------------------ events -> tool
    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and self._space):
            self._pan = (e.position(), self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self.tool is not None:
            self.tool.press(self.mapPos(e), e)
            self.viewport().update()

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            s, hx, vy = self._pan
            d = e.position() - s
            self.horizontalScrollBar().setValue(int(hx - d.x()))
            self.verticalScrollBar().setValue(int(vy - d.y()))
            return
        p = self.mapPos(e)
        self.cursorMoved.emit(p)
        if self.tool is not None:
            self.tool.move(p, e)
            self.viewport().update()

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.setCursor(self.tool.cursor if self.tool else Qt.ArrowCursor)
            return
        if self.tool is not None:
            self.tool.release(self.mapPos(e), e)
            self.viewport().update()

    def mouseDoubleClickEvent(self, e):
        if self.tool is not None:
            self.tool.double(self.mapPos(e), e)
            self.viewport().update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
            return
        if self.tool is not None and self.tool.key(e):
            self.viewport().update()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.setCursor(self.tool.cursor if self.tool else Qt.ArrowCursor)
            return
        super().keyReleaseEvent(e)
