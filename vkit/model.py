"""Shapes and the document: every shape is one path (with fill + outline); transforms are baked into the path."""
import copy
import json
import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainterPath, QPainterPathStroker, QPen, QRadialGradient,
                           QTransform)
from PySide6.QtWidgets import QGraphicsScene, QGraphicsItem, QGraphicsItemGroup, QGraphicsPathItem, QStyle

PX_PER_MM = 96.0 / 25.4

DEFAULT_FILL = {"type": "solid", "c1": "#C2395C", "c2": "#FFFFFF", "angle": 0}
DEFAULT_STROKE = {"color": "#1B1040", "width": 1.0, "dash": "solid", "join": "miter", "cap": "flat"}   # width in px

DASHES = {"solid": None, "dash": [6, 3], "dot": [1, 3], "dashdot": [6, 3, 1, 3]}
JOINS = {"miter": Qt.MiterJoin, "round": Qt.RoundJoin, "bevel": Qt.BevelJoin}
CAPS = {"flat": Qt.FlatCap, "round": Qt.RoundCap, "square": Qt.SquareCap}


# ---------------------------------------------------------------- path <-> segment list
def path_to_segs(path):
    """QPainterPath -> [("M",p) ("L",p) ("C",c1,c2,p) ("Z",)]"""
    segs, i, n = [], 0, path.elementCount()
    start = None
    while i < n:
        e = path.elementAt(i)
        p = QPointF(e.x, e.y)
        if e.type == QPainterPath.MoveToElement:
            if segs and segs[-1][0] != "Z" and start is not None and False:
                pass
            segs.append(("M", p)); start = p
        elif e.type == QPainterPath.LineToElement:
            segs.append(("L", p))
        elif e.type == QPainterPath.CurveToElement:
            c2 = path.elementAt(i + 1); p3 = path.elementAt(i + 2)
            segs.append(("C", p, QPointF(c2.x, c2.y), QPointF(p3.x, p3.y)))
            i += 2
        i += 1
    # closed subpaths: Qt marks closure by the last point equal to the start; keep it as is (a Z is added when equal)
    out, cur = [], None
    for s in segs:
        if s[0] == "M":
            if cur:
                out += cur
            cur = [s]
        else:
            cur.append(s)
    if cur:
        out += cur
    return out


def segs_to_path(segs, closed_flags=None):
    p = QPainterPath()
    for s in segs:
        if s[0] == "M":
            p.moveTo(s[1])
        elif s[0] == "L":
            p.lineTo(s[1])
        elif s[0] == "C":
            p.cubicTo(s[1], s[2], s[3])
        elif s[0] == "Z":
            p.closeSubpath()
    return p


def path_to_json(path):
    out = []
    n, i = path.elementCount(), 0
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.MoveToElement:
            out.append(["M", e.x, e.y])
        elif e.type == QPainterPath.LineToElement:
            out.append(["L", e.x, e.y])
        else:
            a, b = path.elementAt(i + 1), path.elementAt(i + 2)
            out.append(["C", e.x, e.y, a.x, a.y, b.x, b.y]); i += 2
        i += 1
    return out


def path_from_json(data, rule=0):
    p = QPainterPath()
    for s in data:
        if s[0] == "M":
            p.moveTo(s[1], s[2])
        elif s[0] == "L":
            p.lineTo(s[1], s[2])
        elif s[0] == "C":
            p.cubicTo(s[1], s[2], s[3], s[4], s[5], s[6])
        elif s[0] == "Z":
            p.closeSubpath()
    p.setFillRule(Qt.OddEvenFill if rule else Qt.WindingFill)
    return p


def make_star(cx, cy, r1, r2, points):
    p = QPainterPath()
    for i in range(points * 2):
        r = r1 if i % 2 == 0 else r2
        a = -math.pi / 2 + i * math.pi / points
        pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
        p.moveTo(pt) if i == 0 else p.lineTo(pt)
    p.closeSubpath()
    return p


def make_polygon(cx, cy, r, sides):
    p = QPainterPath()
    for i in range(sides):
        a = -math.pi / 2 + i * 2 * math.pi / sides
        pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
        p.moveTo(pt) if i == 0 else p.lineTo(pt)
    p.closeSubpath()
    return p


def make_rect(r, radius=0.0):
    p = QPainterPath()
    if radius > 0:
        p.addRoundedRect(r, radius, radius)
    else:
        p.addRect(r)
    return p


# ---------------------------------------------------------------- shapes

class _IsoSelect:
    """inside an opened group (isolation mode) its objects can be selected one by one; Qt would always select the
    whole group, so that selection is kept by the scene (scene.iso_sel)"""
    def _iso_scene(self):
        sc = self.scene()
        return sc if sc is not None and self.group() is not None and getattr(sc, "iso_group", None) is self.group() else None

    def setSelected(self, on):
        sc = self._iso_scene()
        if sc is None:
            return super().setSelected(on)
        if on and self not in sc.iso_sel:
            sc.iso_sel.append(self)
        elif not on and self in sc.iso_sel:
            sc.iso_sel.remove(self)
        sc.selectionChanged.emit()
        sc.update()

    def isSelected(self):
        sc = self._iso_scene()
        return self in sc.iso_sel if sc is not None else super().isSelected()


class VScene(QGraphicsScene):
    """the drawing's scene; also holds the selection inside an opened group"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.iso_group, self.iso_sel = None, []

    def clearSelection(self):
        had = bool(self.iso_sel)
        self.iso_sel = []
        super().clearSelection()
        if had:
            self.selectionChanged.emit()

class VItem(_IsoSelect, QGraphicsPathItem):
    kind = "path"
    wire = False

    def __init__(self, path=None, fill=None, stroke=None):
        super().__init__()
        self.fill = dict(DEFAULT_FILL if fill is None else fill)
        self.stroke = dict(DEFAULT_STROKE if stroke is None else stroke)
        self.meta = {}                # e.g. text and font for text shapes
        self.name = ""
        self.locked = False
        self.hidden = False
        self.layer = None
        self.layer_locked = False
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.set_path(path or QPainterPath())

    def is_locked(self):
        return self.locked or self.layer_locked

    def set_path(self, path):
        self.setPath(path)
        self.restyle()

    def restyle(self):
        f = self.fill
        r = self.path().boundingRect()
        if f["type"] == "none" or r.isNull() and f["type"] != "solid":
            self.setBrush(Qt.NoBrush)
        elif f["type"] == "solid":
            self.setBrush(QBrush(QColor(f["c1"])))
        else:
            a = math.radians(f.get("angle", 0))
            c = r.center()
            if f["type"] == "linear":
                half = (abs(r.width() * math.cos(a)) + abs(r.height() * math.sin(a))) / 2 or 1
                g = QLinearGradient(c.x() - half * math.cos(a), c.y() - half * math.sin(a), c.x() + half * math.cos(a), c.y() + half * math.sin(a))
            else:
                g = QRadialGradient(c, max(r.width(), r.height()) / 2 or 1)
            g.setColorAt(0, QColor(f["c1"]))
            g.setColorAt(1, QColor(f["c2"]))
            self.setBrush(QBrush(g))
        s = self.stroke
        if s["width"] <= 0 or s["color"] in (None, "none"):
            self.setPen(QPen(Qt.NoPen))
        else:
            pen = QPen(QColor(s["color"]), s["width"])
            pen.setJoinStyle(JOINS.get(s["join"], Qt.MiterJoin))
            pen.setCapStyle(CAPS.get(s["cap"], Qt.FlatCap))
            d = DASHES.get(s["dash"])
            if d:
                pen.setDashPattern(d)
            self.setPen(pen)

    def paint(self, painter, option, widget=None):
        if VItem.wire:                              # wireframe view: outlines only
            painter.setPen(QPen(QColor("#0078FF"), 0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.path())
            return
        option.state &= ~QStyle.State_Selected      # the tools draw their own selection handles
        super().paint(painter, option, widget)

    def shape(self):                   # clicking inside an unfilled shape should not select it, but its outline should
        p = QPainterPathStroker()
        p.setWidth(max(6.0, self.pen().widthF()))
        s = p.createStroke(self.path())
        if self.fill["type"] != "none":
            s = s.united(self.path())
        return s

    def bake(self, t: QTransform):
        self.set_path(t.map(self.path()))
        if self.kind == "text" and self.meta:
            # a pure turn keeps the text editable: remember the angle, the letters are rebuilt from it
            a, b, c, d = t.m11(), t.m12(), t.m21(), t.m22()
            if abs(a * d - b * c - 1) < 1e-4 and abs(a - d) < 1e-6 and abs(b + c) < 1e-6 and abs(b) > 1e-9:
                ang = (self.meta.get("angle", 0) + math.degrees(math.atan2(b, a))) % 360
                self.meta["angle"] = round(ang, 4) if abs(ang) > 1e-6 and abs(ang - 360) > 1e-6 else 0

    def to_json(self):
        return {"t": "item", "kind": self.kind, "path": path_to_json(self.path()), "rule": 1 if self.path().fillRule() == Qt.OddEvenFill else 0,
                "fill": self.fill, "stroke": self.stroke, "opacity": self.opacity(), "name": self.name, "locked": self.locked,
                "hidden": self.hidden, "layer": self.layer, "meta": self.meta}


class VGroup(_IsoSelect, QGraphicsItemGroup):
    def __init__(self):
        super().__init__()
        self.name = ""
        self.locked = False
        self.hidden = False
        self.layer = None
        self.layer_locked = False
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setHandlesChildEvents(True)

    def is_locked(self):
        return self.locked or self.layer_locked

    def paint(self, painter, option, widget=None):
        pass

    def bake(self, t: QTransform):
        for c in self.childItems():
            c.bake(t)

    def leaves(self):
        out = []
        for c in self.childItems():
            out += c.leaves() if isinstance(c, VGroup) else [c]
        return out

    def to_json(self):
        return {"t": "group", "children": [c.to_json() for c in self.childItems()], "opacity": self.opacity(),
                "name": self.name, "locked": self.locked, "hidden": self.hidden, "layer": self.layer}


def item_from_json(d):
    if d["t"] == "group":
        g = VGroup()
        for c in d["children"]:
            g.addToGroup(item_from_json(c))
        g.setOpacity(d.get("opacity", 1))
        g.name, g.locked = d.get("name", ""), d.get("locked", False)
        g.hidden, g.layer = d.get("hidden", False), d.get("layer")
        g.setVisible(not g.hidden)
        return g
    it = VItem(path_from_json(d["path"], d.get("rule", 0)), d["fill"], d["stroke"])
    it.kind = d.get("kind", "path")
    it.meta = d.get("meta", {})
    it.name, it.locked = d.get("name", ""), d.get("locked", False)
    it.setOpacity(d.get("opacity", 1))
    it.hidden, it.layer = d.get("hidden", False), d.get("layer")
    it.setVisible(not it.hidden)
    return it


def leaves_of(item):
    return item.leaves() if isinstance(item, VGroup) else [item]


def scene_rect_of(item):
    return item.sceneBoundingRect()


# ---------------------------------------------------------------- document
class Page:
    """the artboards of the document (as in Illustrator). Everything that asks for "the page" (align to page,
    export, page size, fit) works on the active artboard."""
    GAP_MM = 20.0

    def __init__(self, w_mm=210.0, h_mm=297.0):
        self.boards = [dict(name="Artboard 1", x=0.0, y=0.0, w=float(w_mm), h=float(h_mm))]
        self.active = 0

    @property
    def board(self):
        self.active = max(0, min(self.active, len(self.boards) - 1))
        return self.boards[self.active]

    @property
    def w_mm(self):
        return self.board["w"]

    @w_mm.setter
    def w_mm(self, v):
        self.board["w"] = float(v)

    @property
    def h_mm(self):
        return self.board["h"]

    @h_mm.setter
    def h_mm(self, v):
        self.board["h"] = float(v)

    def rect_of(self, i):
        b = self.boards[i]
        return QRectF(b["x"] * PX_PER_MM, b["y"] * PX_PER_MM, b["w"] * PX_PER_MM, b["h"] * PX_PER_MM)

    @property
    def rect(self):
        self.board
        return self.rect_of(self.active)

    def index_at(self, p):
        for i in range(len(self.boards) - 1, -1, -1):
            if self.rect_of(i).contains(p):
                return i
        return -1

    def add(self, w=None, h=None, x=None, y=None, name=None):
        """a new artboard, by default the size of the active one, to the right of all others"""
        w = float(w if w is not None else self.w_mm)
        h = float(h if h is not None else self.h_mm)
        if x is None:
            x = max(b["x"] + b["w"] for b in self.boards) + self.GAP_MM
            y = min(b["y"] for b in self.boards)
        n = 1
        names = {b["name"] for b in self.boards}
        while f"Artboard {n}" in names:
            n += 1
        self.boards.append(dict(name=name or f"Artboard {n}", x=float(x), y=float(y), w=w, h=h))
        self.active = len(self.boards) - 1
        return self.active

    def to_json(self):
        return {"boards": [dict(b) for b in self.boards], "active": self.active}

    @staticmethod
    def from_json(data):
        p = Page(*data.get("page", [210, 297]))
        if data.get("boards"):
            p.boards = [dict(name=b.get("name", f"Artboard {i + 1}"), x=float(b.get("x", 0)), y=float(b.get("y", 0)),
                             w=float(b["w"]), h=float(b["h"])) for i, b in enumerate(data["boards"])]
            p.active = int(data.get("active_board", 0))
        return p


def dump_scene(scene, page, guides, layers=None):
    items = [i.to_json() for i in scene.items(order=Qt.AscendingOrder) if i.group() is None and isinstance(i, (VItem, VGroup))]
    return {"app": "digonto-vector", "version": 1, "page": [page.boards[0]["w"], page.boards[0]["h"]], "boards": [dict(b) for b in page.boards],
            "active_board": page.active, "items": items, "guides": guides, "layers": layers}


def load_into(scene, data):
    for i in list(scene.items()):
        if isinstance(i, (VItem, VGroup)) and i.group() is None:
            scene.removeItem(i)
    for d in data.get("items", []):
        scene.addItem(item_from_json(d))
    return Page.from_json(data), data.get("guides", {"h": [], "v": []})


DEFAULT_LAYERS = [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False}]


def layers_from(data):
    ly = data.get("layers")
    return [dict(l) for l in ly] if ly else [dict(l) for l in DEFAULT_LAYERS]


def geom_rect(item):
    """bounding box of the real geometry in scene coordinates (without outline width)"""
    if isinstance(item, VGroup):
        r = None
        for c in item.childItems():
            cr = geom_rect(c)
            r = cr if r is None else r.united(cr)
        return r or QRectF()
    return item.mapToScene(item.path().boundingRect()).boundingRect()


def top_level(scene):
    return [i for i in scene.items(order=Qt.AscendingOrder) if i.group() is None and isinstance(i, (VItem, VGroup))]


def union_rect(items):
    r = None
    for it in items:
        g = geom_rect(it)
        if g.isNull() and g.width() == 0 and g.height() == 0 and False:
            continue
        r = g if r is None else r.united(g)
    return r or QRectF()
