"""Commands that work on the selection: group, shaping, align, arrange, transform, contour ..."""
import copy
import json
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QPen, QTransform
from PySide6.QtWidgets import QGraphicsItem

from .nodes import path_from_subs, subs_from_path
from .model import (DASHES, CAPS, JOINS, PX_PER_MM, VGroup, VItem, geom_rect, item_from_json, leaves_of, top_level,
                    union_rect)

CLIP = []          # internal clipboard (json)


def layer_of(it):
    while it is not None and it.group() is not None:
        it = it.group()
    return it.layer if it is not None else None


def ordered(v, items):
    order = top_level(v.scene_)
    return [i for i in order if i in items]


def normalize_z(v):
    v.normalize_z()


def sel(v):
    return ordered(v, v.selected())


def select(v, items):
    v.scene_.clearSelection()
    for i in items:
        i.setSelected(True)


# ---------------------------------------------------------------- edit
def delete(v):
    s = v.selected()
    for it in s:
        v.scene_.removeItem(it)
    if s:
        v.commit("Delete")


def copy_sel(v):
    CLIP[:] = [i.to_json() for i in sel(v)]
    v.message.emit(f"Copied {len(CLIP)} object(s)")


def cut(v):
    copy_sel(v)
    delete(v)


def paste(v, in_place=False):
    if not CLIP:
        return
    off = 0 if in_place else 5 * PX_PER_MM
    new = []
    for d in CLIP:
        it = item_from_json(copy.deepcopy(d))
        it.bake(QTransform.fromTranslate(off, off))
        it.setZValue(1e9 + len(new))
        v.scene_.addItem(it)
        new.append(it)
    normalize_z(v)
    select(v, new)
    if not in_place:
        for d in CLIP:                      # next paste lands a bit further, like Corel
            pass
    v.commit("Paste")


def duplicate(v):
    s = sel(v)
    if not s:
        return
    new = []
    for it in s:
        c = item_from_json(it.to_json())
        c.bake(QTransform.fromTranslate(5 * PX_PER_MM, 5 * PX_PER_MM))
        c.setZValue(1e9 + len(new))
        v.scene_.addItem(c)
        new.append(c)
    normalize_z(v)
    select(v, new)
    v.commit("Duplicate")


def step_repeat(v, copies, dx_mm, dy_mm):
    s = sel(v)
    if not s or copies < 1:
        return
    new = []
    for k in range(1, copies + 1):
        for it in s:
            c = item_from_json(it.to_json())
            c.setZValue(1e9 + len(new))
            c.bake(QTransform.fromTranslate(dx_mm * PX_PER_MM * k, dy_mm * PX_PER_MM * k))
            v.scene_.addItem(c)
            new.append(c)
    normalize_z(v)
    select(v, new)
    v.commit("Step and repeat")


def select_all(v):
    select(v, [i for i in top_level(v.scene_) if i.isVisible() and not i.is_locked()])


def invert_selection(v):
    cur = set(v.selected())
    select(v, [i for i in top_level(v.scene_) if i.isVisible() and not i.is_locked() and i not in cur])


# ---------------------------------------------------------------- arrange
def arrange(v, how):
    s = sel(v)
    if not s:
        return
    order = top_level(v.scene_)
    rest = [i for i in order if i not in s]
    if how == "front":
        order = rest + s
    elif how == "back":
        order = s + rest
    elif how == "forward":
        order = list(order)
        for it in reversed(s):
            k = order.index(it)
            if k < len(order) - 1 and order[k + 1] not in s:
                order[k], order[k + 1] = order[k + 1], order[k]
    elif how == "backward":
        order = list(order)
        for it in s:
            k = order.index(it)
            if k > 0 and order[k - 1] not in s:
                order[k], order[k - 1] = order[k - 1], order[k]
    for n, it in enumerate(order):
        it.setZValue(n)
    v.commit("Arrange")


# ---------------------------------------------------------------- group
def group(v):
    s = sel(v)
    if len(s) < 2:
        return
    z = max(i.zValue() for i in s)
    g = VGroup()
    g.layer = layer_of(s[0])
    v.scene_.addItem(g)
    for it in s:
        g.addToGroup(it)
    g.setZValue(z)
    normalize_z(v)
    select(v, [g])
    v.commit("Group")


def ungroup(v):
    new = []
    for it in sel(v):
        if isinstance(it, VGroup):
            kids = list(it.childItems())
            glayer = it.layer
            z = it.zValue()
            it.setSelected(False)
            for k in kids:
                k.setSelected(False)
            v.scene_.destroyItemGroup(it)
            for k in kids:
                k.layer = glayer
                k.setSelected(False)             # Qt keeps a stale 'selected' state on ungrouped children
                k.setZValue(z)
                k.setFlag(QGraphicsItem.ItemIsSelectable, True)
            new += kids
        else:
            new.append(it)
    normalize_z(v)
    select(v, new)
    v.commit("Ungroup")


def flat(items):
    out = []
    for i in items:
        out += leaves_of(i)
    return out


# ---------------------------------------------------------------- combine / break
def combine(v):
    s = sel(v)
    leaves = flat(s)
    if len(leaves) < 2:
        return
    base = leaves[0]
    p = QPainterPath()
    p.setFillRule(Qt.OddEvenFill)
    for lf in leaves:
        p.addPath(lf.path())
    it = VItem(p, dict(base.fill), dict(base.stroke))
    it.layer = layer_of(base)
    for x in s:
        v.scene_.removeItem(x)
    it.setZValue(max(x.zValue() for x in s))
    v.scene_.addItem(it)
    normalize_z(v)
    select(v, [it])
    v.commit("Combine")


def break_apart(v):
    new = []
    for it in sel(v):
        for lf in flat([it]):
            parts = [path_from_subs([sb]) for sb in subs_from_path(lf.path())]
            if len(parts) < 2:
                new.append(lf)
                continue
            z = lf.zValue()
            for pa in parts:
                n = VItem(pa, dict(lf.fill), dict(lf.stroke))
                n.layer = layer_of(lf)
                n.setZValue(z)
                v.scene_.addItem(n)
                new.append(n)
            g = lf.group()
            if g is not None:
                g.removeFromGroup(lf)
            v.scene_.removeItem(lf)
        if isinstance(it, VGroup) and it.scene() is not None:
            v.scene_.destroyItemGroup(it)
    normalize_z(v)
    select(v, [n for n in new if n.scene() is not None])
    v.commit("Break apart")


# ---------------------------------------------------------------- shaping
def _fill_area(item):
    p = QPainterPath(item.path())
    return p


def shaping(v, mode):
    s = flat(sel(v))
    if len(s) < 2:
        v.message.emit("Select two or more objects first")
        return
    paths = [x.path() for x in s]
    if mode == "weld":
        r = paths[0]
        for p in paths[1:]:
            r = r.united(p)
        style = s[0]
    elif mode == "intersect":
        r = paths[0]
        for p in paths[1:]:
            r = r.intersected(p)
        style = s[0]
    elif mode == "front_minus_back":        # bottom object minus everything in front of it
        r = paths[0]
        for p in paths[1:]:
            r = r.subtracted(p)
        style = s[0]
    elif mode == "back_minus_front":        # top object minus everything behind it
        r = paths[-1]
        for p in paths[:-1]:
            r = r.subtracted(p)
        style = s[-1]
    elif mode == "trim":                    # every object is trimmed by those in front, all stay
        for k in range(len(s) - 1):
            r = paths[k]
            for p in paths[k + 1:]:
                r = r.subtracted(p)
            s[k].set_path(r)
        v.commit("Trim")
        return
    else:
        return
    r = r.simplified()
    if r.isEmpty():
        v.message.emit("Nothing left after shaping")
        return
    it = VItem(r, dict(style.fill), dict(style.stroke))
    it.layer = layer_of(style)
    z = max(x.zValue() for x in sel(v))
    for x in sel(v):
        v.scene_.removeItem(x)
    it.setZValue(z)
    v.scene_.addItem(it)
    normalize_z(v)
    select(v, [it])
    v.commit({"weld": "Weld", "intersect": "Intersect", "front_minus_back": "Front minus back", "back_minus_front": "Back minus front"}[mode])


# ---------------------------------------------------------------- outline / contour
def stroker_for(st):
    k = QPainterPathStroker()
    k.setWidth(max(st["width"], 0.01))
    k.setJoinStyle(JOINS.get(st["join"], Qt.MiterJoin))
    k.setCapStyle(CAPS.get(st["cap"], Qt.FlatCap))
    d = DASHES.get(st["dash"])
    if d:
        k.setDashPattern(d)
    return k


def outline_to_object(v):
    new = []
    for lf in flat(sel(v)):
        st = lf.stroke
        if st["width"] <= 0 or st["color"] in (None, "none"):
            continue
        out = stroker_for(st).createStroke(lf.path()).simplified()
        o = VItem(out, {"type": "solid", "c1": st["color"], "c2": "#FFFFFF", "angle": 0},
                  dict(st, width=0.0))
        o.layer = layer_of(lf)
        o.setZValue(lf.zValue() + 0.5)
        v.scene_.addItem(o)
        lf.stroke = dict(st, width=0.0)
        lf.restyle()
        new.append(o)
    if new:
        normalize_z(v)
        select(v, new)
        v.commit("Outline to object")
    else:
        v.message.emit("The selected objects have no outline")


def contour(v, dist_mm, steps, outward=True):
    new = []
    for lf in flat(sel(v)):
        for k in range(1, steps + 1):
            d = dist_mm * PX_PER_MM * k
            st = QPainterPathStroker()
            st.setWidth(2 * d)
            st.setJoinStyle(Qt.RoundJoin)
            ring = st.createStroke(lf.path())
            base = lf.path()
            r = base.united(ring) if outward else base.subtracted(ring)
            r = r.simplified()
            if r.isEmpty():
                break
            o = VItem(r, {"type": "none", "c1": "#000000", "c2": "#FFFFFF", "angle": 0}, dict(lf.stroke, width=max(lf.stroke["width"], 1.0)))
            o.layer = layer_of(lf)
            o.setZValue(lf.zValue() + 0.1 * k)
            v.scene_.addItem(o)
            new.append(o)
    if new:
        normalize_z(v)
        select(v, new)
        v.commit("Contour")


def to_curves(v):
    n = 0
    for lf in flat(sel(v)):
        if lf.kind == "text":
            lf.kind = "path"
            lf.meta = {}
            n += 1
    if n:
        v.commit("Convert to curves")
    else:
        v.message.emit("Select a text object")


# ---------------------------------------------------------------- transform
def transform_sel(v, fn, label):
    s = sel(v)
    if not s:
        return
    c = union_rect(s).center()
    T = fn(c)
    for it in s:
        it.bake(T)
    v.commit(label)


def mirror(v, horizontal=True):
    def fn(c):
        T = QTransform()
        T.translate(c.x(), c.y()); T.scale(-1 if horizontal else 1, 1 if horizontal else -1); T.translate(-c.x(), -c.y())
        return T
    transform_sel(v, fn, "Mirror")


def rotate(v, deg):
    def fn(c):
        T = QTransform()
        T.translate(c.x(), c.y()); T.rotate(deg); T.translate(-c.x(), -c.y())
        return T
    transform_sel(v, fn, "Rotate")


def set_geometry(v, x=None, y=None, w=None, h=None):
    """values in mm; position = top-left of the selection"""
    s = sel(v)
    if not s:
        return
    r = union_rect(s)
    nx = r.x() if x is None else x * PX_PER_MM
    ny = r.y() if y is None else y * PX_PER_MM
    nw = r.width() if w is None else max(w * PX_PER_MM, 0.01)
    nh = r.height() if h is None else max(h * PX_PER_MM, 0.01)
    sx = nw / r.width() if r.width() > 1e-9 else 1.0
    sy = nh / r.height() if r.height() > 1e-9 else 1.0
    T = QTransform()
    T.translate(nx, ny); T.scale(sx, sy); T.translate(-r.x(), -r.y())
    for it in s:
        it.bake(T)
    v.commit("Position and size")


# ---------------------------------------------------------------- align / distribute
def align(v, mode, to_page=False):
    s = sel(v)
    if not s or (len(s) < 2 and not to_page):
        return
    ref = v.page.rect if to_page else union_rect(s)
    for it in s:
        r = geom_rect(it)
        dx = dy = 0.0
        if mode == "left": dx = ref.left() - r.left()
        elif mode == "right": dx = ref.right() - r.right()
        elif mode == "hcenter": dx = ref.center().x() - r.center().x()
        elif mode == "top": dy = ref.top() - r.top()
        elif mode == "bottom": dy = ref.bottom() - r.bottom()
        elif mode == "vcenter": dy = ref.center().y() - r.center().y()
        if dx or dy:
            it.bake(QTransform.fromTranslate(dx, dy))
    v.commit("Align")


def center_on_page(v):
    align(v, "hcenter", True)
    align(v, "vcenter", True)


def distribute(v, axis, mode="gaps"):
    s = sel(v)
    if len(s) < 3:
        v.message.emit("Select three or more objects")
        return
    horiz = axis == "h"
    key = (lambda r: r.left()) if horiz else (lambda r: r.top())
    s.sort(key=lambda i: key(geom_rect(i)))
    rs = [geom_rect(i) for i in s]
    if mode == "centers":
        cs = [(r.center().x() if horiz else r.center().y()) for r in rs]
        step = (cs[-1] - cs[0]) / (len(s) - 1)
        for k, it in enumerate(s):
            d = cs[0] + step * k - cs[k]
            it.bake(QTransform.fromTranslate(d, 0) if horiz else QTransform.fromTranslate(0, d))
    else:
        size = (lambda r: r.width()) if horiz else (lambda r: r.height())
        total = (rs[-1].right() - rs[0].left()) if horiz else (rs[-1].bottom() - rs[0].top())
        gap = (total - sum(size(r) for r in rs)) / (len(s) - 1)
        pos = rs[0].left() if horiz else rs[0].top()
        for k, it in enumerate(s):
            cur = rs[k].left() if horiz else rs[k].top()
            d = pos - cur
            it.bake(QTransform.fromTranslate(d, 0) if horiz else QTransform.fromTranslate(0, d))
            pos += size(rs[k]) + gap
    v.commit("Distribute")


# ---------------------------------------------------------------- lock / hide
def lock(v, on=True):
    for it in v.selected():
        it.locked = on
        if on:
            it.setSelected(False)
    v.commit("Lock" if on else "Unlock")


def unlock_all(v):
    for it in top_level(v.scene_):
        it.locked = False
    v.commit("Unlock all")


def hide(v):
    for it in v.selected():
        it.hidden = True
        it.setSelected(False)
    v.commit("Hide")


def show_all(v):
    for it in top_level(v.scene_):
        it.hidden = False
    v.commit("Show all")


# ---------------------------------------------------------------- style
def set_style(v, fill=None, stroke=None, opacity=None, label="Style"):
    changed = False
    for lf in flat(v.selected()):
        if fill is not None:
            lf.fill = dict(lf.fill, **fill)
            changed = True
        if stroke is not None:
            lf.stroke = dict(lf.stroke, **stroke)
            changed = True
        lf.restyle()
    if opacity is not None:
        for it in v.selected():
            it.setOpacity(opacity)
            changed = True
    if fill is not None:
        v.fill = dict(v.fill, **fill)
    if stroke is not None:
        v.stroke = dict(v.stroke, **stroke)
    if changed:
        v.commit(label)


# ---------------------------------------------------------------- more tools (Illustrator / Corel style)
def clip_to_top(v):
    """the front object is the frame: everything else is cut to its outline (the frame is removed)"""
    s = sel(v)
    if len(s) < 2:
        v.message.emit("Select the objects and, in front of them, the shape to clip with")
        return
    mask = flat([s[-1]])
    mp = QPainterPath()
    for m in mask:
        mp.addPath(m.path())
    n = 0
    for it in s[:-1]:
        for lf in flat([it]):
            if lf.fill["type"] == "none":
                continue
            lf.set_path(lf.path().intersected(mp).simplified())
            n += 1
    v.scene_.removeItem(s[-1])
    v.commit("Clip to shape")


def _mix(a, b, t):
    from PySide6.QtGui import QColor
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def blend(v, steps):
    """steps copies between the lowest and the highest selected object: position, size, colours and outline are mixed"""
    lv = flat(sel(v))
    if len(lv) < 2:
        v.message.emit("Select two objects")
        return
    A, B = lv[0], lv[-1]
    ra, rb = A.path().boundingRect(), B.path().boundingRect()
    ga, gb = A.path().boundingRect(), B.path().boundingRect()
    new = []
    for k in range(1, steps + 1):
        t = k / (steps + 1.0)
        x = ra.x() + (rb.x() - ra.x()) * t; y = ra.y() + (rb.y() - ra.y()) * t
        w = ra.width() + (rb.width() - ra.width()) * t; h = ra.height() + (rb.height() - ra.height()) * t
        T = QTransform()
        T.translate(x, y)
        T.scale(w / ra.width() if ra.width() > 1e-9 else 1.0, h / ra.height() if ra.height() > 1e-9 else 1.0)
        T.translate(-ra.x(), -ra.y())
        it = VItem(T.map(A.path()), dict(A.fill), dict(A.stroke))
        it.fill = dict(A.fill, c1=_mix(A.fill["c1"], B.fill["c1"], t), c2=_mix(A.fill["c2"], B.fill["c2"], t))
        if A.fill["type"] != B.fill["type"] and B.fill["type"] != "none" and A.fill["type"] == "none":
            it.fill["type"] = B.fill["type"]
        it.stroke = dict(A.stroke, color=_mix(A.stroke["color"] or "#000000", B.stroke["color"] or "#000000", t),
                         width=A.stroke["width"] + (B.stroke["width"] - A.stroke["width"]) * t)
        it.restyle()
        it.layer = layer_of(A)
        it.setZValue(A.zValue() + (B.zValue() - A.zValue()) * t)
        v.scene_.addItem(it)
        new.append(it)
    normalize_z(v)
    select(v, new)
    v.commit("Blend")


def round_corners(v, radius_mm):
    """round the straight corners of the selected shapes (like Corel's Fillet / Scallop tool)"""
    from .nodes import Node, Sub, path_from_subs, subs_from_path
    r0 = radius_mm * PX_PER_MM
    c = 0.5523
    done = 0
    for lf in flat(sel(v)):
        subs = subs_from_path(lf.path())
        for sb in subs:
            ns = sb.nodes
            m = len(ns)
            out = []
            for i, n in enumerate(ns):
                has_nb = sb.closed or (0 < i < m - 1)
                if not has_nb or n.hin is not None or n.hout is not None:
                    out.append(n)
                    continue
                a, b = ns[(i - 1) % m], ns[(i + 1) % m]
                if a.hout is not None or b.hin is not None:
                    out.append(n)
                    continue
                va, vb = a.p - n.p, b.p - n.p
                la, lb = math.hypot(va.x(), va.y()), math.hypot(vb.x(), vb.y())
                if la < 1e-6 or lb < 1e-6:
                    out.append(n)
                    continue
                r = min(r0, la / 2, lb / 2)
                p1 = n.p + QPointF(va.x() / la * r, va.y() / la * r)
                p2 = n.p + QPointF(vb.x() / lb * r, vb.y() / lb * r)
                out.append(Node(p1, hout=p1 + (n.p - p1) * c, smooth=False))
                out.append(Node(p2, hin=p2 + (n.p - p2) * c, smooth=False))
                done += 1
            sb.nodes = out
        lf.set_path(path_from_subs(subs, lf.path().fillRule()))
    if done:
        v.commit("Round corners")
    else:
        v.message.emit("No straight corners to round")


def skew(v, ax_deg, ay_deg):
    def fn(c):
        T = QTransform()
        T.translate(c.x(), c.y())
        T *= QTransform(1, math.tan(math.radians(ay_deg)), math.tan(math.radians(ax_deg)), 1, 0, 0)
        T.translate(-c.x(), -c.y())
        return T
    transform_sel(v, fn, "Skew")


def trace_file(v, path, **kw):
    from PySide6.QtGui import QImage
    from .trace import trace_image
    res, w, h = trace_image(QImage(path), **kw)
    if not res:
        v.message.emit("Nothing found to trace - try another threshold")
        return 0
    pr = v.page.rect
    sc = min(pr.width() * 0.9 / w, pr.height() * 0.9 / h)
    T = QTransform()
    T.translate(pr.center().x() - w * sc / 2, pr.center().y() - h * sc / 2)
    T.scale(sc, sc)
    items = []
    for p, col in res:
        pa = T.map(p)
        pa.setFillRule(Qt.OddEvenFill)
        it = VItem(pa, {"type": "solid", "c1": col, "c2": "#FFFFFF", "angle": 0}, dict(v.stroke, width=0.0))
        v.scene_.addItem(it)
        items.append(it)
    select(v, items)
    if len(items) > 1:
        v.commit("Image trace")
        group(v)
    else:
        v.commit("Image trace")
    return len(items)
