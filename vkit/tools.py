"""Tools: select, node edit, pen, shapes, pencil, text, eyedropper, zoom, hand."""
import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QApplication, QInputDialog

from .model import PX_PER_MM, VGroup, VItem, geom_rect, make_polygon, make_rect, make_star, top_level, union_rect
from .nodes import Node, Sub, make_smooth, path_from_subs, simplify, smooth_polyline, subs_from_path

BLUE = QColor(0, 120, 255)
PINK = QColor(255, 0, 140)


def new_item(v, path, fill=True):
    it = VItem(path, dict(v.fill), dict(v.stroke))
    if not fill:
        it.fill = dict(it.fill, type="none")
        if it.stroke["width"] <= 0 or it.stroke["color"] in (None, "none"):
            it.stroke = dict(it.stroke, width=1.0, color="#1B1040")
        it.restyle()
    return it


def top_of(it):
    while it is not None and it.group() is not None:      # (parentItem() would make PySide delete the item)
        it = it.group()
    return it


def hit_item(v, pos, tol_px=5):
    t = tol_px / v.zoom_factor()
    for it in v.scene_.items(QRectF(pos.x() - t, pos.y() - t, 2 * t, 2 * t), Qt.IntersectsItemShape, Qt.DescendingOrder):
        top = top_of(it)
        if isinstance(top, (VItem, VGroup)) and top.isVisible() and not top.is_locked():
            return top
    for it in v.scene_.items(QRectF(pos.x(), pos.y(), 0.01, 0.01), Qt.IntersectsItemBoundingRect, Qt.DescendingOrder):    # text: the whole box counts
        top = top_of(it)
        if isinstance(top, VItem) and top.kind == "text" and top.isVisible() and not top.is_locked():
            return top
    return None


def text_path(meta, origin=QPointF(0, 0)):
    f = QFont(meta.get("family", "Sans Serif"))
    f.setPixelSize(max(1, int(round(meta.get("size", 48)))))
    f.setBold(meta.get("bold", False))
    f.setItalic(meta.get("italic", False))
    p = QPainterPath()
    lh = QFontMetricsF_height(f)
    for i, line in enumerate(meta.get("text", "").split("\n")):
        p.addText(0, i * lh, f, line)
    a = meta.get("angle", 0)
    if a:
        p = QTransform().rotate(a).map(p)              # turned around the start of the first baseline
    p.translate(origin)
    return p


def hit_text(v, pos):
    """the text object under pos, also inside a group (a table number in a seating plan ...)"""
    t = 3 / v.zoom_factor()
    for it in v.scene_.items(QRectF(pos.x() - t, pos.y() - t, 2 * t, 2 * t), Qt.IntersectsItemBoundingRect, Qt.DescendingOrder):
        if isinstance(it, VItem) and it.kind == "text" and it.isVisible() and not top_of(it).is_locked():
            return it
    return None


def QFontMetricsF_height(f):
    from PySide6.QtGui import QFontMetricsF
    return QFontMetricsF(f).lineSpacing()


class Tool:
    cursor = Qt.ArrowCursor
    dragging = False

    def __init__(self, v):
        self.v = v

    def activate(self): pass
    def deactivate(self): pass
    def press(self, p, e): pass
    def move(self, p, e): pass
    def release(self, p, e): pass
    def double(self, p, e): pass
    def key(self, e): return False
    def paint(self, painter, z): pass


# ============================================================ select
class SelectTool(Tool):
    cursor = Qt.ArrowCursor

    def __init__(self, v):
        super().__init__(v)
        self.mode = None
        self.orig = []

    def bbox(self):
        s = self.v.selected()
        return union_rect(s) if s else None

    def handles(self, r, z):
        pts = [r.topLeft(), QPointF(r.center().x(), r.top()), r.topRight(), QPointF(r.right(), r.center().y()),
               r.bottomRight(), QPointF(r.center().x(), r.bottom()), r.bottomLeft(), QPointF(r.left(), r.center().y())]
        pts.append(QPointF(r.center().x(), r.top() - 24 / z))
        return pts

    def hit_handle(self, p):
        r = self.bbox()
        if r is None:
            return None
        z = self.v.zoom_factor()
        for i, h in enumerate(self.handles(r, z)):
            if abs(h.x() - p.x()) < 6 / z and abs(h.y() - p.y()) < 6 / z:
                return i
        return None

    def press(self, p, e):
        v = self.v
        if e.button() != Qt.LeftButton:
            return
        self.start = QPointF(p)
        self.moved = False
        h = self.hit_handle(p)
        sel = v.selected()
        if h is not None:
            self.mode = "rotate" if h == 8 else "scale"
            self.h = h
            self.r0 = self.bbox()
            self.snapshot()
            self.dragging = True
            return
        it = hit_item(v, p)
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if it is not None:
            if shift:
                it.setSelected(not it.isSelected())
                self.mode = None
                return
            if not it.isSelected():
                v.scene_.clearSelection()
                it.setSelected(True)
            self.mode = "move"
            self.r0 = self.bbox()
            self.snapshot()
            self.dragging = True
            if e.modifiers() & Qt.ControlModifier:
                self.mode = "move"
        else:
            if not shift:
                v.scene_.clearSelection()
            self.mode = "band"
            self.band = QRectF(p, p)
            self.dragging = True

    def snapshot(self):
        self.orig = []
        for it in self.v.selected():
            for lf in (it.leaves() if isinstance(it, VGroup) else [it]):
                self.orig.append((lf, lf.path()))

    def apply(self, T):
        for lf, path in self.orig:
            lf.set_path(T.map(path))
        self.moved = True

    def move(self, p, e):
        v = self.v
        if not self.dragging:
            return
        if self.mode == "band":
            self.band = QRectF(self.start, p).normalized()
            return
        if self.mode == "move":
            d = p - self.start
            if not self.moved and math.hypot(d.x(), d.y()) * v.zoom_factor() < 3:
                return
            if e.modifiers() & Qt.ShiftModifier:
                if abs(d.x()) > abs(d.y()):
                    d = QPointF(d.x(), 0)
                else:
                    d = QPointF(0, d.y())
            r = self.r0
            best = [d.x(), d.y()]
            bdx = bdy = 1e9
            if not (e.modifiers() & Qt.AltModifier):
                for q in (r.topLeft(), r.center(), r.bottomRight()):
                    s = v.snap_pt(q + d, exclude=v.selected())
                    ax, ay = s.x() - (q.x() + d.x()), s.y() - (q.y() + d.y())
                    if abs(ax) > 1e-9 and abs(ax) < bdx:
                        bdx, best[0] = abs(ax), d.x() + ax
                    if abs(ay) > 1e-9 and abs(ay) < bdy:
                        bdy, best[1] = abs(ay), d.y() + ay
                if bdx > 1e8 and bdy > 1e8:
                    v._snap_marker = None
            T = QTransform.fromTranslate(best[0], best[1])
            self.apply(T)
            self.last_d = QPointF(*best)
            return
        if self.mode == "scale":
            r = self.r0
            hp = self.handles(r, 1)[self.h]
            ax = {0: r.right(), 1: r.center().x(), 2: r.left(), 3: r.left(), 4: r.left(), 5: r.center().x(), 6: r.right(), 7: r.right()}[self.h]
            ay = {0: r.bottom(), 1: r.bottom(), 2: r.bottom(), 3: r.center().y(), 4: r.top(), 5: r.top(), 6: r.top(), 7: r.center().y()}[self.h]
            if e.modifiers() & Qt.AltModifier:          # alt: scale from the centre
                ax, ay = r.center().x(), r.center().y()
            q = v.snap_pt(p, exclude=v.selected())
            sx = sy = 1.0
            if self.h in (0, 2, 3, 4, 6, 7):
                dx = hp.x() - ax
                sx = (q.x() - ax) / dx if abs(dx) > 1e-9 else 1.0
            if self.h in (0, 1, 2, 4, 5, 6):
                dy = hp.y() - ay
                sy = (q.y() - ay) / dy if abs(dy) > 1e-9 else 1.0
            if self.h in (0, 2, 4, 6) and not (e.modifiers() & Qt.ShiftModifier):
                pass
            elif self.h in (0, 2, 4, 6):                 # shift on corners: keep proportions
                s = sx if abs(sx) > abs(sy) else sy
                sx = sy = s
            if abs(sx) < 1e-3: sx = 1e-3
            if abs(sy) < 1e-3: sy = 1e-3
            T = QTransform()
            T.translate(ax, ay); T.scale(sx, sy); T.translate(-ax, -ay)
            self.apply(T)
            return
        if self.mode == "rotate":
            c = self.r0.center()
            a0 = math.degrees(math.atan2(self.start.y() - c.y(), self.start.x() - c.x()))
            a1 = math.degrees(math.atan2(p.y() - c.y(), p.x() - c.x()))
            ang = a1 - a0
            if e.modifiers() & Qt.ShiftModifier:
                ang = round(ang / 15.0) * 15.0
            T = QTransform()
            T.translate(c.x(), c.y()); T.rotate(ang); T.translate(-c.x(), -c.y())
            self.apply(T)

    def release(self, p, e):
        v = self.v
        self.dragging = False
        v._snap_marker = None
        if self.mode == "band":
            for it in top_level(v.scene_):
                if it.isVisible() and not it.is_locked() and self.band.contains(geom_rect(it)):
                    it.setSelected(True)
            self.mode = None
            return
        if self.mode in ("move", "scale", "rotate") and self.moved:
            v.commit({"move": "Move", "scale": "Resize", "rotate": "Rotate"}[self.mode])
        self.mode = None
        self.orig = []

    def double(self, p, e):
        txt = hit_text(self.v, p)
        if txt is not None and txt.group() is not None:   # text inside a group: edit it in place
            self.v.tools["text"].edit(txt)
            return
        it = hit_item(self.v, p)
        if it is None:
            return
        self.v.scene_.clearSelection()
        it.setSelected(True)
        if isinstance(it, VItem) and it.kind == "text":
            self.v.toolRequest.emit("text")
        elif isinstance(it, VItem):
            self.v.toolRequest.emit("node")

    def key(self, e):
        v = self.v
        k = e.key()
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            for it in v.selected():
                v.scene_.removeItem(it)
            v.commit("Delete")
            return True
        if k == Qt.Key_Escape:
            v.scene_.clearSelection()
            return True
        d = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}.get(k)
        if d and v.selected():
            step = (10 if e.modifiers() & Qt.ShiftModifier else 1) * (PX_PER_MM if not (e.modifiers() & Qt.AltModifier) else 0.1)
            T = QTransform.fromTranslate(d[0] * step, d[1] * step)
            for it in v.selected():
                it.bake(T)
            v.commit("Nudge")
            return True
        return False

    def paint(self, pt, z):
        v = self.v
        if self.mode == "band" and self.dragging:
            pt.setPen(QPen(BLUE, 0, Qt.DashLine)); pt.setBrush(QColor(0, 120, 255, 30))
            pt.drawRect(self.band)
            return
        r = self.bbox()
        if r is None:
            return
        pt.setPen(QPen(BLUE, 0, Qt.DashLine)); pt.setBrush(Qt.NoBrush)
        pt.drawRect(r)
        hs = self.handles(r, z)
        pt.drawLine(hs[1], hs[8])
        pt.setPen(QPen(BLUE, 0)); pt.setBrush(QColor("#FFFFFF"))
        s = 4 / z
        for i, h in enumerate(hs):
            if i == 8:
                pt.drawEllipse(h, s, s)
            else:
                pt.drawRect(QRectF(h.x() - s, h.y() - s, 2 * s, 2 * s))


# ============================================================ node edit
class NodeTool(Tool):
    cursor = Qt.ArrowCursor

    def __init__(self, v):
        super().__init__(v)
        self.item = None
        self.subs = []
        self.sel = set()
        self.drag = None

    def activate(self):
        self.v.selectionChanged2.connect(self.refresh)
        self.refresh()

    def deactivate(self):
        try:
            self.v.selectionChanged2.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        self.item = None

    def refresh(self):
        s = self.v.selected()
        it = s[0] if len(s) == 1 and isinstance(s[0], VItem) else None
        if it is not self.item:
            self.item = it
            self.sel = set()
            self.subs = subs_from_path(it.path()) if it is not None else []
        elif it is not None and self.drag is None:
            self.subs = subs_from_path(it.path())
            self.sel = {k for k in self.sel if k[0] < len(self.subs) and k[1] < len(self.subs[k[0]].nodes)}

    def apply(self):
        if self.item is not None:
            self.item.set_path(path_from_subs(self.subs, self.item.path().fillRule()))

    # ----- hit testing
    def tol(self):
        return 7 / self.v.zoom_factor()

    def hit(self, p):
        t = self.tol()
        for (si, ni) in self.sel:                       # handles first
            n = self.subs[si].nodes[ni]
            for which in ("hin", "hout"):
                h = getattr(n, which)
                if h is not None and abs(h.x() - p.x()) < t and abs(h.y() - p.y()) < t:
                    return ("h", si, ni, which)
        for si, sb in enumerate(self.subs):
            for ni, n in enumerate(sb.nodes):
                if abs(n.p.x() - p.x()) < t and abs(n.p.y() - p.y()) < t:
                    return ("n", si, ni)
        seg = self.hit_segment(p)
        if seg:
            return ("s",) + seg
        return None

    def seg_points(self, si, i):
        sb = self.subs[si]
        a, b = sb.nodes[i], sb.nodes[(i + 1) % len(sb.nodes)]
        return a, b, [a.p, a.hout if a.hout is not None else a.p, b.hin if b.hin is not None else b.p, b.p]

    def seg_count(self, sb):
        return len(sb.nodes) if sb.closed else len(sb.nodes) - 1

    def hit_segment(self, p):
        t = self.tol()
        best = None
        for si, sb in enumerate(self.subs):
            for i in range(self.seg_count(sb)):
                _, _, P = self.seg_points(si, i)
                for k in range(0, 41):
                    u = k / 40
                    q = bez(P, u)
                    d = math.hypot(q.x() - p.x(), q.y() - p.y())
                    if d < t and (best is None or d < best[0]):
                        best = (d, si, i, u)
        return best[1:] if best else None

    # ----- events
    def press(self, p, e):
        if e.button() != Qt.LeftButton:
            return
        v = self.v
        if self.item is None:
            it = hit_item(v, p)
            if it is not None and isinstance(it, VItem):
                v.scene_.clearSelection(); it.setSelected(True)
            return
        h = self.hit(p)
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        self.start = QPointF(p)
        self.moved = False
        self.dragging = True
        if h is None:
            it = hit_item(v, p)
            if it is not None and isinstance(it, VItem) and it is not self.item:
                v.scene_.clearSelection(); it.setSelected(True)
                self.dragging = False
                return
            if not shift:
                self.sel = set()
            self.drag = ("band", QRectF(p, p))
            return
        if h[0] == "h":
            self.drag = ("handle", h[1], h[2], h[3])
        elif h[0] == "n":
            k = (h[1], h[2])
            if shift:
                self.sel ^= {k}
            elif k not in self.sel:
                self.sel = {k}
            self.drag = ("nodes", dict())
            self.origs = {kk: self.subs[kk[0]].nodes[kk[1]].copy() for kk in self.sel}
        else:
            si, i, u = h[1], h[2], h[3]
            sb = self.subs[si]
            self.sel = {(si, i), (si, (i + 1) % len(sb.nodes))}
            self.drag = ("seg", si, i, u)
            self.origs = {kk: self.subs[kk[0]].nodes[kk[1]].copy() for kk in self.sel}

    def move(self, p, e):
        if not self.dragging or self.drag is None:
            return
        kind = self.drag[0]
        d = p - self.start
        if not self.moved and math.hypot(d.x(), d.y()) * self.v.zoom_factor() < 2:
            return
        self.moved = True
        if kind == "band":
            self.drag = ("band", QRectF(self.start, p).normalized())
        elif kind == "nodes":
            q = self.v.snap_pt(p, exclude=[self.item]) if len(self.sel) == 1 else p
            if len(self.sel) == 1:
                d = q - self.start
            if e.modifiers() & Qt.ShiftModifier:
                d = QPointF(d.x(), 0) if abs(d.x()) > abs(d.y()) else QPointF(0, d.y())
            for (si, ni), o in self.origs.items():
                n = self.subs[si].nodes[ni]
                n.p = o.p + d
                n.hin = o.hin + d if o.hin is not None else None
                n.hout = o.hout + d if o.hout is not None else None
            self.apply()
        elif kind == "handle":
            _, si, ni, which = self.drag
            n = self.subs[si].nodes[ni]
            setattr(n, which, QPointF(p))
            other = "hout" if which == "hin" else "hin"
            o = getattr(n, other)
            if n.smooth and not (e.modifiers() & Qt.AltModifier):
                a = getattr(n, which) - n.p
                la = math.hypot(a.x(), a.y())
                if la > 1e-9:
                    lo = math.hypot((o - n.p).x(), (o - n.p).y()) if o is not None else la
                    setattr(n, other, n.p - QPointF(a.x() / la * lo, a.y() / la * lo))
            self.apply()
        elif kind == "seg":                        # drag a curve segment: reshape it
            _, si, i, u = self.drag
            a, b = self.origs[(si, i)], self.origs[(si, (i + 1) % len(self.subs[si].nodes))]
            na, nb = self.subs[si].nodes[i], self.subs[si].nodes[(i + 1) % len(self.subs[si].nodes)]
            P1 = a.hout if a.hout is not None else a.p
            P2 = b.hin if b.hin is not None else b.p
            u = min(max(u, 0.1), 0.9)
            k = 1.0 / (3 * u * (1 - u))        # shifting both handles by d*k moves the curve point at u by d
            na.hout = P1 + d * k
            nb.hin = P2 + d * k
            na.smooth = nb.smooth = False
            self.apply()

    def release(self, p, e):
        self.dragging = False
        d = self.drag
        self.drag = None
        if d is None:
            return
        if d[0] == "band":
            r = d[1]
            if not self.moved:
                self.sel = set()
            for si, sb in enumerate(self.subs):
                for ni, n in enumerate(sb.nodes):
                    if r.contains(n.p):
                        self.sel.add((si, ni))
        elif self.moved:
            self.v.commit("Edit nodes")
            self.refresh()

    def double(self, p, e):
        if self.item is None:
            return
        h = self.hit(p)
        if h and h[0] == "s":
            self.insert_at(h[1], h[2], h[3])
        elif h and h[0] == "n":
            self.toggle_smooth(h[1], h[2])

    def key(self, e):
        k = e.key()
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.sel:
                self.delete_nodes()
            return True
        if k == Qt.Key_Escape:
            self.sel = set()
            return True
        d = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}.get(k)
        if d and self.sel:
            step = (10 if e.modifiers() & Qt.ShiftModifier else 1) * 0.5 * PX_PER_MM
            off = QPointF(d[0] * step, d[1] * step)
            for (si, ni) in self.sel:
                n = self.subs[si].nodes[ni]
                n.p += off
                if n.hin is not None: n.hin += off
                if n.hout is not None: n.hout += off
            self.apply(); self.v.commit("Nudge nodes")
            return True
        return False

    # ----- operations (menu / panel buttons)
    def _finish(self, label):
        self.apply()
        self.v.commit(label)
        self.refresh()
        self.v.viewport().update()

    def insert_at(self, si, i, u):
        sb = self.subs[si]
        a, b, P = self.seg_points(si, i)
        if a.hout is None and b.hin is None:
            q = bez(P, u)
            n = Node(q)
        else:
            P01 = lerp(P[0], P[1], u); P12 = lerp(P[1], P[2], u); P23 = lerp(P[2], P[3], u)
            P012 = lerp(P01, P12, u); P123 = lerp(P12, P23, u); Q = lerp(P012, P123, u)
            a.hout = P01
            b.hin = P23
            n = Node(Q, hin=P012, hout=P123, smooth=True)
        sb.nodes.insert(i + 1, n)
        self.sel = {(si, i + 1)}
        self._finish("Add node")

    def add_nodes(self):
        """add a node in the middle of every segment between two selected nodes"""
        done = False
        for si, sb in enumerate(self.subs):
            idx = sorted(k[1] for k in self.sel if k[0] == si)
            for i in reversed(idx):
                j = (i + 1) % len(sb.nodes)
                if j in idx and (i + 1 < len(sb.nodes) or sb.closed):
                    self.insert_at(si, i, 0.5)
                    done = True
        if not done and self.sel:
            si, ni = sorted(self.sel)[0]
            if ni < self.seg_count(self.subs[si]):
                self.insert_at(si, ni, 0.5)

    def delete_nodes(self):
        for si in sorted({k[0] for k in self.sel}, reverse=True):
            sb = self.subs[si]
            kill = {k[1] for k in self.sel if k[0] == si}
            sb.nodes = [n for i, n in enumerate(sb.nodes) if i not in kill]
            if len(sb.nodes) < 2:
                del self.subs[si]
        self.sel = set()
        if not self.subs:
            self.v.scene_.removeItem(self.item)
            self.item = None
            self.v.commit("Delete")
            return
        self._finish("Delete nodes")

    def set_lines(self):
        self._seg_convert(False)

    def set_curves(self):
        self._seg_convert(True)

    def _seg_convert(self, curve):
        for si, sb in enumerate(self.subs):
            for i in range(self.seg_count(sb)):
                j = (i + 1) % len(sb.nodes)
                if (si, i) in self.sel or (si, j) in self.sel:
                    a, b = sb.nodes[i], sb.nodes[j]
                    if curve and a.hout is None and b.hin is None:
                        a.hout = a.p + (b.p - a.p) * (1 / 3)
                        b.hin = b.p + (a.p - b.p) * (1 / 3)
                    elif not curve:
                        a.hout = None; b.hin = None
        self._finish("To curve" if curve else "To line")

    def toggle_smooth(self, si, ni):
        self.sel = {(si, ni)}
        self.make_smooth()

    def make_smooth(self):
        for (si, ni) in self.sel:
            sb = self.subs[si]
            m = len(sb.nodes)
            prev = sb.nodes[(ni - 1) % m] if (ni > 0 or sb.closed) else None
            nxt = sb.nodes[(ni + 1) % m] if (ni < m - 1 or sb.closed) else None
            make_smooth(sb.nodes[ni], prev, nxt)
        self._finish("Smooth node")

    def make_cusp(self):
        for (si, ni) in self.sel:
            self.subs[si].nodes[ni].smooth = False
            n = self.subs[si].nodes[ni]
            n.hin = None if n.hin is None else n.hin
        self._finish("Cusp node")

    def close_path(self):
        for si in {k[0] for k in self.sel} or set(range(len(self.subs))):
            self.subs[si].closed = True
        self._finish("Close path")

    def break_at(self):
        for (si, ni) in sorted(self.sel, reverse=True):
            sb = self.subs[si]
            if sb.closed:
                ns = sb.nodes[ni:] + sb.nodes[:ni + 1]
                ns[-1] = ns[-1].copy()
                ns[0].hin = None
                ns[-1].hout = None
                sb.nodes, sb.closed = ns, False
            else:
                a = sb.nodes[:ni + 1]
                b = [n.copy() for n in sb.nodes[ni:]]
                a[-1] = a[-1].copy(); a[-1].hout = None
                b[0].hin = None
                sb.nodes = a
                self.subs.insert(si + 1, Sub(b, False))
        self.sel = set()
        self._finish("Break path")

    def paint(self, pt, z):
        if self.item is None:
            return
        pt.setPen(QPen(BLUE, 0)); pt.setBrush(Qt.NoBrush)
        pt.drawPath(self.item.path())
        s = 3.5 / z
        for si, sb in enumerate(self.subs):
            for ni, n in enumerate(sb.nodes):
                sel = (si, ni) in self.sel
                if sel:
                    pt.setPen(QPen(BLUE, 0))
                    for h in (n.hin, n.hout):
                        if h is not None:
                            pt.drawLine(n.p, h)
                            pt.setBrush(QColor("#FFFFFF")); pt.drawEllipse(h, s * 0.8, s * 0.8)
                pt.setPen(QPen(BLUE, 0))
                pt.setBrush(BLUE if sel else QColor("#FFFFFF"))
                r = QRectF(n.p.x() - s, n.p.y() - s, 2 * s, 2 * s)
                pt.drawEllipse(r) if n.smooth else pt.drawRect(r)
        if self.drag and self.drag[0] == "band":
            pt.setPen(QPen(BLUE, 0, Qt.DashLine)); pt.setBrush(QColor(0, 120, 255, 30))
            pt.drawRect(self.drag[1])


def lerp(a, b, u):
    return QPointF(a.x() + (b.x() - a.x()) * u, a.y() + (b.y() - a.y()) * u)


def bez(P, u):
    a, b, c = lerp(P[0], P[1], u), lerp(P[1], P[2], u), lerp(P[2], P[3], u)
    return lerp(lerp(a, b, u), lerp(b, c, u), u)


# ============================================================ pen
class PenTool(Tool):
    cursor = Qt.CrossCursor

    def __init__(self, v):
        super().__init__(v)
        self.nodes = []
        self.cur = None

    def deactivate(self):
        self.finish()

    def press(self, p, e):
        if e.button() == Qt.RightButton:
            self.finish()
            return
        if e.button() != Qt.LeftButton:
            return
        q = self.v.snap_pt(p)
        t = 8 / self.v.zoom_factor()
        if len(self.nodes) >= 3 and abs(q.x() - self.nodes[0].p.x()) < t and abs(q.y() - self.nodes[0].p.y()) < t:
            self.finish(closed=True)
            return
        self.nodes.append(Node(q))
        self.dragging = True
        self.cur = q

    def move(self, p, e):
        q = self.v.snap_pt(p)
        if e.modifiers() & Qt.ShiftModifier and self.nodes:
            last = self.nodes[-1].p
            d = q - last
            ang = round(math.atan2(d.y(), d.x()) / (math.pi / 4)) * (math.pi / 4)
            L = math.hypot(d.x(), d.y())
            q = QPointF(last.x() + L * math.cos(ang), last.y() + L * math.sin(ang))
        self.cur = q
        if self.dragging and self.nodes:
            n = self.nodes[-1]
            if math.hypot(q.x() - n.p.x(), q.y() - n.p.y()) * self.v.zoom_factor() > 3:
                n.hout = QPointF(q)
                n.hin = n.p - (q - n.p)
                n.smooth = True

    def release(self, p, e):
        self.dragging = False

    def double(self, p, e):
        if len(self.nodes) > 1 and math.hypot(self.nodes[-1].p.x() - self.nodes[-2].p.x(), self.nodes[-1].p.y() - self.nodes[-2].p.y()) < 1:
            self.nodes.pop()
        self.finish()

    def key(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(); return True
        if e.key() == Qt.Key_Escape:
            self.nodes = []; return True
        if e.key() == Qt.Key_Backspace and self.nodes:
            self.nodes.pop(); return True
        return False

    def finish(self, closed=False):
        nodes, self.nodes = self.nodes, []
        self.dragging = False
        if len(nodes) < 2:
            return
        if closed and nodes[0].hin is None and nodes[-1].hout is None:
            pass
        path = path_from_subs([Sub(nodes, closed)])
        it = new_item(self.v, path, fill=closed)
        self.v.add_item(it, "Pen")

    def paint(self, pt, z):
        if not self.nodes:
            return
        pre = [n.copy() for n in self.nodes]
        pt.setPen(QPen(BLUE, 0))
        pt.setBrush(Qt.NoBrush)
        if self.cur is not None and not self.dragging:
            pre.append(Node(self.cur))
        pt.drawPath(path_from_subs([Sub(pre)]))
        s = 3.5 / z
        for i, n in enumerate(self.nodes):
            pt.setBrush(BLUE if i == 0 else QColor("#FFFFFF"))
            pt.drawRect(QRectF(n.p.x() - s, n.p.y() - s, 2 * s, 2 * s))
            for h in (n.hin, n.hout):
                if h is not None:
                    pt.drawLine(n.p, h); pt.drawEllipse(h, s * 0.7, s * 0.7)


# ============================================================ shapes
class ShapeTool(Tool):
    cursor = Qt.CrossCursor

    def __init__(self, v, kind):
        super().__init__(v)
        self.kind = kind
        self.item = None

    def press(self, p, e):
        if e.button() != Qt.LeftButton:
            return
        self.a = self.v.snap_pt(p)
        self.item = None
        self.dragging = True

    def build(self, a, b, mods):
        o = self.v.opt
        if self.kind == "line":
            if mods & Qt.ShiftModifier:
                d = b - a
                ang = round(math.atan2(d.y(), d.x()) / (math.pi / 12)) * (math.pi / 12)
                L = math.hypot(d.x(), d.y())
                b = QPointF(a.x() + L * math.cos(ang), a.y() + L * math.sin(ang))
            pth = QPainterPath(a); pth.lineTo(b)
            return pth
        if self.kind in ("polygon", "star"):
            d = b - a
            r = math.hypot(d.x(), d.y())
            if r < 0.5:
                return None
            ang = math.degrees(math.atan2(d.y(), d.x())) + 90
            if mods & Qt.ShiftModifier:
                ang = round(ang / 15) * 15
            pth = make_polygon(a.x(), a.y(), r, int(o["sides"])) if self.kind == "polygon" else make_star(a.x(), a.y(), r, r * o["inner"], int(o["points"]))
            T = QTransform(); T.translate(a.x(), a.y()); T.rotate(ang); T.translate(-a.x(), -a.y())
            return T.map(pth)
        # rect / ellipse
        x0, y0, x1, y1 = a.x(), a.y(), b.x(), b.y()
        if mods & Qt.ShiftModifier:
            s = max(abs(x1 - x0), abs(y1 - y0))
            x1 = x0 + math.copysign(s, x1 - x0 or 1); y1 = y0 + math.copysign(s, y1 - y0 or 1)
        if mods & Qt.ControlModifier:             # from centre
            x0, y0 = 2 * a.x() - x1, 2 * a.y() - y1
        r = QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()
        if r.width() < 0.5 or r.height() < 0.5:
            return None
        if self.kind == "ellipse":
            pth = QPainterPath(); pth.addEllipse(r)
            return pth
        rad = min(o["radius"] * PX_PER_MM, min(r.width(), r.height()) / 2)
        return make_rect(r, rad)

    def move(self, p, e):
        if not self.dragging:
            return
        b = self.v.snap_pt(p)
        pth = self.build(self.a, b, e.modifiers())
        if pth is None:
            return
        if self.item is None:
            self.item = new_item(self.v, pth, fill=(self.kind != "line"))
            self.v.scene_.addItem(self.item)
        else:
            self.item.set_path(pth)

    def release(self, p, e):
        self.dragging = False
        self.v._snap_marker = None
        if self.item is not None:
            it, self.item = self.item, None
            self.v.scene_.clearSelection()
            it.setSelected(True)
            self.v.commit("Draw " + self.kind)

    def key(self, e):
        if e.key() == Qt.Key_Escape and self.item is not None:
            self.v.scene_.removeItem(self.item); self.item = None; self.dragging = False
            return True
        return False


# ============================================================ pencil
class PencilTool(Tool):
    cursor = Qt.CrossCursor

    def __init__(self, v):
        super().__init__(v)
        self.pts = []
        self.item = None

    def press(self, p, e):
        if e.button() != Qt.LeftButton:
            return
        self.pts = [QPointF(p)]
        self.dragging = True
        self.item = new_item(self.v, QPainterPath(p), fill=False)
        self.v.scene_.addItem(self.item)

    def move(self, p, e):
        if not self.dragging:
            return
        if math.hypot(p.x() - self.pts[-1].x(), p.y() - self.pts[-1].y()) * self.v.zoom_factor() < 2:
            return
        self.pts.append(QPointF(p))
        pa = QPainterPath(self.pts[0])
        for q in self.pts[1:]:
            pa.lineTo(q)
        self.item.set_path(pa)

    def release(self, p, e):
        if not self.dragging:
            return
        self.dragging = False
        it, self.item = self.item, None
        z = self.v.zoom_factor()
        pts = simplify(self.pts, 2.0 / z)
        if len(pts) < 2:
            self.v.scene_.removeItem(it)
            return
        closed = len(pts) > 3 and math.hypot(pts[0].x() - pts[-1].x(), pts[0].y() - pts[-1].y()) * z < 8
        if closed:
            pts = pts[:-1]
        it.set_path(path_from_subs([Sub(smooth_polyline(pts, closed), closed)]))
        self.v.scene_.clearSelection()
        it.setSelected(True)
        self.v.commit("Pencil")


# ============================================================ text
class TextTool(Tool):
    cursor = Qt.IBeamCursor

    def activate(self):
        s = self.v.selected()
        if len(s) == 1 and isinstance(s[0], VItem) and s[0].kind == "text":
            self.edit(s[0])

    def press(self, p, e):
        if e.button() != Qt.LeftButton:
            return
        it = hit_text(self.v, p)
        if it is not None:
            self.edit(it)
            return
        meta = dict(self.v.text_opt)
        text, ok = QInputDialog.getMultiLineText(self.v, "Text", "Type your text:", "")
        if not ok or not text.strip():
            return
        meta["text"] = text
        item = new_item(self.v, text_path(meta, p))
        item.kind = "text"
        item.meta = meta
        item.stroke = dict(item.stroke, width=0.0)
        item.restyle()
        self.v.add_item(item, "Text")

    def edit(self, it):
        import digonto_ui as ui
        if not ui.warn_missing_font(self.v.window(), it.meta.get("family")):
            return
        text, ok = QInputDialog.getMultiLineText(self.v, "Edit text", "Text:", it.meta.get("text", ""))
        if not ok or not text.strip():
            return
        top_left = it.path().boundingRect().topLeft()
        old = text_path(it.meta)
        origin = it.path().boundingRect().topLeft() - old.boundingRect().topLeft()
        before = it.path().boundingRect()
        it.meta["text"] = text
        p = text_path(it.meta, origin)
        anchor = it.meta.get("anchor")
        if anchor in ("middle", "end"):                  # centred labels (table numbers ...) stay centred
            after = p.boundingRect()
            if anchor == "middle" or it.meta.get("angle"):
                p.translate(before.center() - after.center())
            else:
                p.translate(before.right() - after.right(), 0)
        it.set_path(p)
        self.v.commit("Edit text")

    def restyle_selected(self):
        """apply font options from the panel to the selected text objects"""
        changed = False
        for it in self.v.selected():
            if isinstance(it, VItem) and it.kind == "text":
                old = text_path(it.meta)
                origin = it.path().boundingRect().topLeft() - old.boundingRect().topLeft()
                it.meta.update({k: self.v.text_opt[k] for k in ("family", "size", "bold", "italic")})
                it.set_path(text_path(it.meta, origin))
                changed = True
        if changed:
            self.v.commit("Font")


# ============================================================ eyedropper / zoom / hand
class EyedropperTool(Tool):
    cursor = Qt.CrossCursor

    def press(self, p, e):
        it = hit_item(self.v, p)
        if not isinstance(it, VItem):
            return
        self.v.fill = dict(it.fill)
        self.v.stroke = dict(it.stroke)
        targets = [t for s in self.v.selected() for t in (s.leaves() if isinstance(s, VGroup) else [s]) if t is not it]
        for t in targets:
            t.fill = dict(it.fill)
            t.stroke = dict(it.stroke)
            t.restyle()
        if targets:
            self.v.commit("Copy style")
        self.v.message.emit("Style picked" + (" and applied" if targets else ""))
        self.v.toolRequest.emit("_style_changed")


class ZoomTool(Tool):
    cursor = Qt.CrossCursor

    def press(self, p, e):
        self.a = QPointF(p)
        self.b = QPointF(p)
        self.out = e.button() == Qt.RightButton or bool(e.modifiers() & Qt.ShiftModifier)
        self.dragging = True

    def move(self, p, e):
        self.b = QPointF(p)

    def release(self, p, e):
        self.dragging = False
        r = QRectF(self.a, self.b).normalized()
        v = self.v
        if r.width() * v.zoom_factor() > 8 and not self.out:
            v.zoom_rect(r)
        else:
            v.zoom_to(v.zoom_factor() * (0.5 if self.out else 2.0))
            v.centerOn(p)

    def paint(self, pt, z):
        if self.dragging and not self.out:
            pt.setPen(QPen(BLUE, 0, Qt.DashLine)); pt.setBrush(Qt.NoBrush)
            pt.drawRect(QRectF(self.a, self.b).normalized())


class HandTool(Tool):
    cursor = Qt.OpenHandCursor

    def press(self, p, e):
        self.s = (e.position(), self.v.horizontalScrollBar().value(), self.v.verticalScrollBar().value())
        self.dragging = True

    def move(self, p, e):
        if self.dragging:
            d = e.position() - self.s[0]
            self.v.horizontalScrollBar().setValue(int(self.s[1] - d.x()))
            self.v.verticalScrollBar().setValue(int(self.s[2] - d.y()))

    def release(self, p, e):
        self.dragging = False
