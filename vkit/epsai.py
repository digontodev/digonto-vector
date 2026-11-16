"""EPS and AI for Digonto Vector.

Export   .eps (Level 3, real paths: opens and edits in Illustrator and Inkscape) and .ai (a PDF-compatible file, Illustrator opens it).
Import   .ai and .pdf (read with PyMuPDF: paths, colours, gradients, text turned into outlines)
         .eps (own small PostScript reader: Illustrator, Inkscape, cairo and Digonto EPS files; no Ghostscript needed)
Shapes become normal editable Vector shapes. Pictures inside the file and soft masks are not imported.
"""
import math
import re
import zlib

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QTransform

from .model import DEFAULT_FILL, DEFAULT_STROKE, PX_PER_MM, Page, VGroup, VItem, leaves_of

K = 96.0 / 72.0                 # points -> Vector pixels
MM_PT = 72.0 / 25.4


# ======================================================================== export (EPS)
def _hex(c):
    q = QColor(c)
    return f"{q.redF():.4f} {q.greenF():.4f} {q.blueF():.4f} setrgbcolor"


def _ps_path(path, t=None):
    out = ["newpath"]
    n, i = path.elementCount(), 0
    start = None
    while i < n:
        e = path.elementAt(i)
        pt = QPointF(e.x, e.y)
        if t is not None:
            pt = t.map(pt)
        if e.type == QPainterPath.MoveToElement:
            out.append(f"{pt.x():.3f} {pt.y():.3f} moveto")
            start = pt
        elif e.type == QPainterPath.LineToElement:
            out.append(f"{pt.x():.3f} {pt.y():.3f} lineto")
        else:
            a, b = path.elementAt(i + 1), path.elementAt(i + 2)
            pa, pb = QPointF(a.x, a.y), QPointF(b.x, b.y)
            if t is not None:
                pa, pb = t.map(pa), t.map(pb)
            out.append(f"{pt.x():.3f} {pt.y():.3f} {pa.x():.3f} {pa.y():.3f} {pb.x():.3f} {pb.y():.3f} curveto")
            i += 2
        i += 1
        nxt = path.elementAt(i) if i < n else None
        if start is not None and (nxt is None or nxt.type == QPainterPath.MoveToElement):
            last = QPointF(path.elementAt(i - 1).x, path.elementAt(i - 1).y)
            if t is not None:
                last = t.map(last)
            if abs(last.x() - start.x()) < 1e-3 and abs(last.y() - start.y()) < 1e-3:
                out.append("closepath")
    return out


def _grad_ps(it, tpath):
    """a gradient in EPS Level 3: the shape is the clip and shfill paints a real gradient (Illustrator and Inkscape read it)"""
    f = it.fill
    c1, c2 = QColor(f["c1"]), QColor(f["c2"])
    r = tpath.boundingRect()
    cx, cy = r.center().x(), r.center().y()
    if f["type"] == "linear":
        a = math.radians(f.get("angle", 0))
        dx, dy = math.cos(a), math.sin(a)
        half = (abs(r.width() * dx) + abs(r.height() * dy)) / 2 or 1
        typ, coords = 2, f"{cx - dx * half:.3f} {cy - dy * half:.3f} {cx + dx * half:.3f} {cy + dy * half:.3f}"
    else:
        rad = max(r.width(), r.height()) / 2 or 1
        typ, coords = 3, f"{cx:.3f} {cy:.3f} 0 {cx:.3f} {cy:.3f} {rad:.3f}"
    fn = (f"<< /FunctionType 2 /Domain [0 1] /C0 [{c1.redF():.4f} {c1.greenF():.4f} {c1.blueF():.4f}] "
          f"/C1 [{c2.redF():.4f} {c2.greenF():.4f} {c2.blueF():.4f}] /N 1 >>")
    out = ["gsave"] + _ps_path(tpath) + ["clip", "newpath",
           f"<< /ShadingType {typ} /ColorSpace /DeviceRGB /Coords [{coords}] /Function {fn} /Extend [true true] >> shfill", "grestore"]
    return out


def export_eps(scene, page, path, items=None, title="Digonto Vector"):
    W, H = page.w_mm * MM_PT, page.h_mm * MM_PT
    out = ["%!PS-Adobe-3.0 EPSF-3.0", f"%%Title: {title}", "%%Creator: Digonto Vector",
           f"%%BoundingBox: 0 0 {int(math.ceil(W))} {int(math.ceil(H))}", f"%%HiResBoundingBox: 0 0 {W:.3f} {H:.3f}",
           "%%LanguageLevel: 3", "%%Pages: 1", "%%EndComments", "%%BeginProlog", "%%EndProlog", "%%Page: 1 1",
           f"gsave 0 {H:.3f} translate {1 / K:.6f} {-1 / K:.6f} scale"]
    top = items if items is not None else [i for i in scene.items(order=Qt.AscendingOrder) if i.group() is None and isinstance(i, (VItem, VGroup))]
    leaves = []
    for it in top:
        leaves += leaves_of(it)
    for it in leaves:
        if not it.isVisible():
            continue
        t = it.sceneTransform()
        p = t.map(it.path())
        if p.isEmpty():
            continue
        f, s = it.fill, it.stroke
        rule = "eofill" if p.fillRule() == Qt.OddEvenFill else "fill"
        if f["type"] in ("linear", "radial"):
            out += _grad_ps(it, p)
        elif f["type"] == "solid":
            out.append(_hex(f["c1"]))
            out += _ps_path(p)
            out.append(rule)
        w = s["width"]
        if w > 0 and s["color"] not in (None, "none"):
            out.append(_hex(s["color"]))
            out.append(f"{w:.3f} setlinewidth {({'miter': 0, 'round': 1, 'bevel': 2})[s['join']]} setlinejoin {({'flat': 0, 'round': 1, 'square': 2})[s['cap']]} setlinecap")
            from .model import DASHES
            d = DASHES.get(s["dash"])
            out.append("[" + " ".join(f"{x * max(1, w):.2f}" for x in d) + "] 0 setdash" if d else "[] 0 setdash")
            out += _ps_path(p)
            out.append("stroke")
    out += ["grestore", "showpage", "%%Trailer", "%%EOF", ""]
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(out))


# ======================================================================== shared: a drawing being read
def _cmyk(c, m, y, k):
    return QColor.fromRgbF(max(0, min(1, (1 - c) * (1 - k))), max(0, min(1, (1 - m) * (1 - k))), max(0, min(1, (1 - y) * (1 - k)))).name()


def _rgb(r, g, b):
    return QColor.fromRgbF(max(0, min(1, r)), max(0, min(1, g)), max(0, min(1, b))).name()


def _gray(g):
    return _rgb(g, g, g)


class State:
    def __init__(self):
        self.ctm = QTransform()
        self.fill = "#000000"          # hex, or a gradient dict
        self.stroke = "#000000"
        self.width = 1.0
        self.join, self.cap = "miter", "flat"
        self.dash = None
        self.alpha = 1.0
        self.clip = None

    def copy(self):
        s = State()
        s.__dict__.update(self.__dict__)
        s.ctm = QTransform(self.ctm)
        return s


class Sink:
    """collects the shapes; base maps drawing units to Vector pixels"""

    def __init__(self, base):
        self.base = base
        self.items = []
        self.skipped = 0

    only_gradients = False

    def add(self, path, fill, stroke_color, width, join, cap, dash, even_odd, alpha=1.0, scale=1.0):
        if path.isEmpty():
            return
        if self.only_gradients:
            if not isinstance(fill, dict):
                return
            stroke_color = None
        path.setFillRule(Qt.OddEvenFill if even_odd else Qt.WindingFill)
        if isinstance(fill, dict):
            fl = dict(fill)
        elif fill:
            fl = {"type": "solid", "c1": fill, "c2": "#FFFFFF", "angle": 0}
        else:
            fl = {"type": "none", "c1": "#000000", "c2": "#FFFFFF", "angle": 0}
        st = dict(DEFAULT_STROKE)
        if stroke_color:
            st.update({"color": stroke_color, "width": max(0.01, width * scale), "join": join, "cap": cap})
            if dash:
                a = [abs(x) for x in dash if x is not None]
                st["dash"] = "dot" if (a and max(a) <= 1.5 * max(width, 0.5)) else ("dashdot" if len(a) >= 4 else "dash")
        else:
            st.update({"width": 0, "color": "none"})
        # Inkscape / cairo / Digonto paint the fill and then the outline as two operations on the same path: that is one shape
        if self.items and not stroke_color_is_none(st) and fl["type"] == "none":
            last = self.items[-1]
            if last.stroke.get("width", 0) == 0 and last.fill["type"] != "none" and last.path() == path:
                last.stroke = st
                return
        it = VItem(path, fl, st)
        if alpha < 0.999:
            it.setOpacity(max(0.0, alpha))
        self.items.append(it)


def stroke_color_is_none(st):
    return st.get("width", 0) == 0 or st.get("color") in (None, "none")


def _dash_ok(d):
    return d if d and any(x > 0 for x in d) else None


# ======================================================================== PostScript / EPS reader
_TOK = re.compile(rb"%[^\r\n]*|\((?:\\.|[^()\\])*\)|<<|>>|<[0-9A-Fa-f\s]*>|\{|\}|\[|\]|/?[^\s\[\]{}()<>/%]+", re.S)


def _tokens(data):
    toks = []
    depth_str = 0
    for m in _TOK.finditer(data):
        t = m.group(0)
        if t.startswith(b"%"):
            continue
        toks.append(t)
    return toks


def _parse(toks):
    """tokens -> nested lists for { } procedures"""
    root, stack = [], []
    cur = root
    for t in toks:
        if t == b"{":
            n = []
            cur.append(n)
            stack.append(cur)
            cur = n
        elif t == b"}":
            if stack:
                cur = stack.pop()
        else:
            cur.append(t)
    return root


class PS:
    """a small PostScript machine: enough for paths, colours and transforms (Illustrator, Inkscape, cairo, Digonto)"""

    def __init__(self, sink):
        self.sink = sink
        self.st = []
        self.gs = State()
        self.gs.ctm = QTransform()
        self.stack = []
        self.defs = {}
        self.path = QPainterPath()
        self.cur = None
        self.start = None
        self.compound = None            # Illustrator *u ... *U
        self.comp_paint = None
        self.steps = 0
        self.ops = self._ops()

    # ---- helpers
    def pt(self, x, y):
        return self.gs.ctm.map(QPointF(x, y))

    def pop(self):
        return self.stack.pop()

    def popn(self, n):
        v = self.stack[-n:]
        del self.stack[-n:]
        return v

    def scale_px(self):
        c = self.gs.ctm
        return math.sqrt(abs(c.m11() * c.m22() - c.m12() * c.m21())) or 1.0

    def moveto(self, x, y):
        p = self.pt(x, y)
        self.path.moveTo(p)
        self.cur, self.start = (x, y), (x, y)

    def lineto(self, x, y):
        if self.cur is None:
            return self.moveto(x, y)
        self.path.lineTo(self.pt(x, y))
        self.cur = (x, y)

    def curveto(self, x1, y1, x2, y2, x3, y3):
        if self.cur is None:
            self.moveto(x1, y1)
        self.path.cubicTo(self.pt(x1, y1), self.pt(x2, y2), self.pt(x3, y3))
        self.cur = (x3, y3)

    def closepath(self):
        if self.start is not None and self.cur is not None:
            self.path.closeSubpath()
            self.cur = self.start

    def paint(self, fill, stroke, even=False, close=False):
        if close:
            self.closepath()
        if self.compound is not None:
            self.compound.addPath(self.path)
            if fill or stroke:
                self.comp_paint = (fill, stroke, even)
            self.path = QPainterPath()
            self.cur = self.start = None
            return
        self._emit(self.path, fill, stroke, even)
        self.path = QPainterPath()
        self.cur = self.start = None

    def _emit(self, path, fill, stroke, even):
        g = self.gs
        if not (fill or stroke):
            return
        self.sink.add(QPainterPath(path), g.fill if fill else None, g.stroke if stroke else None, g.width, g.join, g.cap, g.dash, even,
                      g.alpha, self.scale_px() * K if False else self.scale_px())

    def newpath(self):
        self.path = QPainterPath()
        self.cur = self.start = None

    # ---- the operators
    def _ops(self):
        P = self

        def num2(f):
            def run():
                b, a = P.pop(), P.pop()
                P.stack.append(f(a, b))
            return run

        def num1(f):
            def run():
                P.stack.append(f(P.pop()))
            return run

        def setrgb():
            b, g, r = P.pop(), P.pop(), P.pop()
            P.gs.fill = P.gs.stroke = _rgb(r, g, b)

        def gray_fill():
            P.gs.fill = _gray(P.pop())

        def gray_stroke():
            P.gs.stroke = _gray(P.pop())

        def cmyk_fill():
            k, y, m, c = P.pop(), P.pop(), P.pop(), P.pop()
            P.gs.fill = _cmyk(c, m, y, k)

        def cmyk_stroke():
            k, y, m, c = P.pop(), P.pop(), P.pop(), P.pop()
            P.gs.stroke = _cmyk(c, m, y, k)

        def setcmyk():
            k, y, m, c = P.pop(), P.pop(), P.pop(), P.pop()
            P.gs.fill = P.gs.stroke = _cmyk(c, m, y, k)

        def custom_fill():          # Illustrator:  c m y k (name) tint x
            P.pop()
            tint = P.pop() if False else None
            k, y, m, c = None, None, None, None
            vals = [v for v in P.stack[-6:] if isinstance(v, (int, float))]
            if len(vals) >= 4:
                c, m, y, k = vals[-5:-1] if len(vals) >= 5 else vals[:4]
                t = vals[-1] if len(vals) >= 5 else 1.0
                P.gs.fill = _cmyk(c * t, m * t, y * t, k * t)
            del P.stack[-6:]

        def custom_stroke():
            P.pop()
            vals = [v for v in P.stack[-6:] if isinstance(v, (int, float))]
            if len(vals) >= 4:
                c, m, y, k = vals[-5:-1] if len(vals) >= 5 else vals[:4]
                t = vals[-1] if len(vals) >= 5 else 1.0
                P.gs.stroke = _cmyk(c * t, m * t, y * t, k * t)
            del P.stack[-6:]

        def sethsb():
            b, s, h = P.pop(), P.pop(), P.pop()
            P.gs.fill = P.gs.stroke = QColor.fromHsvF(max(0, min(.999, h)), max(0, min(1, s)), max(0, min(1, b))).name()

        def moveto():
            y, x = P.pop(), P.pop()
            P.moveto(x, y)

        def lineto():
            y, x = P.pop(), P.pop()
            P.lineto(x, y)

        def rmoveto():
            dy, dx = P.pop(), P.pop()
            c = P.cur or (0, 0)
            P.moveto(c[0] + dx, c[1] + dy)

        def rlineto():
            dy, dx = P.pop(), P.pop()
            c = P.cur or (0, 0)
            P.lineto(c[0] + dx, c[1] + dy)

        def curveto():
            v = P.popn(6)
            P.curveto(*v)

        def rcurveto():
            v = P.popn(6)
            c = P.cur or (0, 0)
            P.curveto(c[0] + v[0], c[1] + v[1], c[0] + v[2], c[1] + v[3], c[0] + v[4], c[1] + v[5])

        def v_op():                 # Illustrator v: first control point = current point
            v = P.popn(4)
            c = P.cur or (v[0], v[1])
            P.curveto(c[0], c[1], *v)

        def y_op():                 # Illustrator y: second control point = end point
            v = P.popn(4)
            P.curveto(v[0], v[1], v[2], v[3], v[2], v[3])

        def arc(ccw=True):
            def run():
                a2, a1, r, y, x = P.pop(), P.pop(), P.pop(), P.pop(), P.pop()
                if not ccw:
                    a1, a2 = a2, a1
                    # clockwise: sweep from a1 down to a2
                    sweep = (a2 - a1) % 360 if False else -((a1 - a2) % 360 or 360)
                    start, sweep = a1, sweep
                    pts = self_arc(x, y, r, start, sweep)
                else:
                    sweep = (a2 - a1) % 360 or 360
                    pts = self_arc(x, y, r, a1, sweep)
                P_arc(pts)
            return run

        def self_arc(cx, cy, r, a1, sweep):
            n = max(1, int(math.ceil(abs(sweep) / 90.0)))
            seg = sweep / n
            out = []
            for i in range(n):
                s = math.radians(a1 + seg * i)
                e = math.radians(a1 + seg * (i + 1))
                k = 4.0 / 3.0 * math.tan((e - s) / 4.0)
                out.append((cx + r * (math.cos(s) - k * math.sin(s)), cy + r * (math.sin(s) + k * math.cos(s)),
                            cx + r * (math.cos(e) + k * math.sin(e)), cy + r * (math.sin(e) - k * math.cos(e)),
                            cx + r * math.cos(e), cy + r * math.sin(e)))
            return (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)), out)

        def P_arc(pts):
            x0, y0, segs = pts
            if P.cur is None:
                P.moveto(x0, y0)
            else:
                P.lineto(x0, y0)
            for s in segs:
                P.curveto(*s)

        def closepath():
            P.closepath()

        def fill():
            P.paint(True, False, False)

        def eofill():
            P.paint(True, False, True)

        def stroke():
            P.paint(False, True)

        def clip():
            c = QPainterPath(P.path)
            P.gs.clip = c if P.gs.clip is None else P.gs.clip.intersected(c)

        def gsave():
            P.st.append((P.gs.copy(), QPainterPath(P.path), P.cur, P.start))

        def grestore():
            if P.st:
                P.gs, P.path, P.cur, P.start = P.st.pop()

        def translate():
            y, x = P.pop(), P.pop()
            P.gs.ctm = QTransform.fromTranslate(x, y) * P.gs.ctm

        def scale():
            y, x = P.pop(), P.pop()
            P.gs.ctm = QTransform.fromScale(x, y) * P.gs.ctm

        def rotate():
            a = P.pop()
            P.gs.ctm = QTransform().rotate(a) * P.gs.ctm

        def concat():
            m = P.pop()
            if isinstance(m, list) and len(m) == 6:
                P.gs.ctm = QTransform(*[float(x) for x in m]) * P.gs.ctm

        def setlinewidth():
            P.gs.width = abs(P.pop())

        def setlinejoin():
            P.gs.join = ("miter", "round", "bevel")[int(P.pop()) % 3]

        def setlinecap():
            P.gs.cap = ("flat", "round", "square")[int(P.pop()) % 3]

        def setdash():
            off, arr = P.pop(), P.pop()
            P.gs.dash = _dash_ok([float(x) for x in arr]) if isinstance(arr, list) else None

        def rectfill():
            h, w, y, x = P.pop(), P.pop(), P.pop(), P.pop()
            sv = (P.path, P.cur, P.start)
            P.newpath()
            P.moveto(x, y)
            P.lineto(x + w, y)
            P.lineto(x + w, y + h)
            P.lineto(x, y + h)
            P.paint(True, False, False, True)
            P.path, P.cur, P.start = sv

        def rectstroke():
            h, w, y, x = P.pop(), P.pop(), P.pop(), P.pop()
            sv = (P.path, P.cur, P.start)
            P.newpath()
            P.moveto(x, y)
            P.lineto(x + w, y)
            P.lineto(x + w, y + h)
            P.lineto(x, y + h)
            P.paint(False, True, False, True)
            P.path, P.cur, P.start = sv

        def rectclip():
            h, w, y, x = P.pop(), P.pop(), P.pop(), P.pop()
            c = QPainterPath()
            for k, (px, py) in enumerate(((x, y), (x + w, y), (x + w, y + h), (x, y + h))):
                (c.moveTo if k == 0 else c.lineTo)(P.pt(px, py))
            c.closeSubpath()
            P.gs.clip = c if P.gs.clip is None else P.gs.clip.intersected(c)

        def concat_v(m):
            P.gs.ctm = QTransform(*[float(x) for x in m]) * P.gs.ctm

        def re_op():                                   # PDF-style rectangle (cairo)
            h, w, y, x = P.pop(), P.pop(), P.pop(), P.pop()
            P.moveto(x, y); P.lineto(x + w, y); P.lineto(x + w, y + h); P.lineto(x, y + h); P.closepath()

        def array_op():
            P.stack.append([0] * int(P.pop()))

        def astore():
            a = P.pop()
            n = len(a)
            vals = P.stack[-n:] if n else []
            del P.stack[len(P.stack) - n:]
            P.stack.append(list(vals))

        def pdef():
            v, k = P.pop(), P.pop()
            if isinstance(k, bytes):
                P.defs[k.lstrip(b"/")] = v

        def exch():
            b, a = P.pop(), P.pop()
            P.stack += [b, a]

        def dup():
            P.stack.append(P.stack[-1])

        def pop():
            P.pop()

        def roll():
            j, n = int(P.pop()), int(P.pop())
            if n > 0 and len(P.stack) >= n:
                part = P.stack[-n:]
                j %= n
                part = part[-j:] + part[:-j] if j else part
                P.stack[-n:] = part

        def index():
            n = int(P.pop())
            P.stack.append(P.stack[-1 - n])

        def copy():
            n = int(P.pop())
            if n > 0:
                P.stack += P.stack[-n:]

        def mark():
            P.stack.append(b"__mark__")

        def dict_open():
            P.stack.append(b"__dmark__")

        def dict_close():
            i = len(P.stack) - 1
            while i >= 0 and P.stack[i] != b"__dmark__":
                i -= 1
            items = P.stack[i + 1:]
            del P.stack[i:]
            d = {}
            for k in range(0, len(items) - 1, 2):
                if isinstance(items[k], bytes):
                    d[items[k].lstrip(b"/")] = items[k + 1]
            P.stack.append(d)

        def shfill():
            d = P.pop()
            if isinstance(d, dict) and P.gs.clip is not None:
                fill = _ps_shading(d, P.gs.ctm)
                if fill:
                    P.sink.add(QPainterPath(P.gs.clip), fill, None, 0, "miter", "flat", None, False, P.gs.alpha, 1.0)

        def arr_end():
            i = len(P.stack) - 1
            while i >= 0 and P.stack[i] != b"__mark__":
                i -= 1
            a = P.stack[i + 1:]
            del P.stack[i:]
            P.stack.append(a)

        def pif():
            proc, c = P.pop(), P.pop()
            if c:
                P.run(proc)

        def ifelse():
            p2, p1, c = P.pop(), P.pop(), P.pop()
            P.run(p1 if c else p2)

        def repeat():
            proc, n = P.pop(), int(P.pop())
            for _ in range(min(n, 5000)):
                P.run(proc)

        def pfor():
            proc, lim, inc, ini = P.pop(), P.pop(), P.pop(), P.pop()
            v, n = ini, 0
            while ((inc > 0 and v <= lim) or (inc < 0 and v >= lim)) and n < 5000:
                P.stack.append(v)
                P.run(proc)
                v += inc
                n += 1

        def cmp(f):
            def run():
                b, a = P.pop(), P.pop()
                P.stack.append(f(a, b))
            return run

        def ill_ctm():              # Illustrator  ... d  (dash)
            off, arr = P.pop(), P.pop()
            P.gs.dash = _dash_ok([float(x) for x in arr]) if isinstance(arr, list) else None

        o = {
            b"add": num2(lambda a, b: a + b), b"sub": num2(lambda a, b: a - b), b"mul": num2(lambda a, b: a * b),
            b"div": num2(lambda a, b: a / b if b else 0.0), b"idiv": num2(lambda a, b: int(a / b) if b else 0),
            b"mod": num2(lambda a, b: a % b if b else 0), b"neg": num1(lambda a: -a), b"abs": num1(abs),
            b"sqrt": num1(lambda a: math.sqrt(max(a, 0))), b"cvr": num1(float), b"cvi": num1(int), b"round": num1(round),
            b"floor": num1(math.floor), b"ceiling": num1(math.ceil), b"truncate": num1(math.trunc),
            b"sin": num1(lambda a: math.sin(math.radians(a))), b"cos": num1(lambda a: math.cos(math.radians(a))),
            b"eq": cmp(lambda a, b: a == b), b"ne": cmp(lambda a, b: a != b), b"gt": cmp(lambda a, b: a > b),
            b"lt": cmp(lambda a, b: a < b), b"ge": cmp(lambda a, b: a >= b), b"le": cmp(lambda a, b: a <= b),
            b"and": cmp(lambda a, b: bool(a) and bool(b) if isinstance(a, bool) else int(a) & int(b)),
            b"or": cmp(lambda a, b: bool(a) or bool(b) if isinstance(a, bool) else int(a) | int(b)),
            b"not": num1(lambda a: (not a) if isinstance(a, bool) else ~int(a)), b"true": lambda: P.stack.append(True),
            b"false": lambda: P.stack.append(False), b"exch": exch, b"dup": dup, b"pop": pop, b"roll": roll, b"index": index, b"copy": copy,
            b"clear": lambda: P.stack.clear(), b"if": pif, b"ifelse": ifelse, b"repeat": repeat, b"for": pfor, b"def": pdef,
            b"bind": lambda: None, b"mark": mark, b"[": mark, b"]": arr_end,
            b"<<": dict_open, b">>": dict_close, b"shfill": shfill, b"newpath": P.newpath, b"moveto": moveto, b"lineto": lineto, b"rmoveto": rmoveto, b"rlineto": rlineto, b"curveto": curveto,
            b"rcurveto": rcurveto, b"closepath": closepath, b"arc": arc(True), b"arcn": arc(False), b"fill": fill, b"eofill": eofill,
            b"stroke": stroke, b"clip": clip, b"eoclip": clip, b"rectfill": rectfill, b"rectstroke": rectstroke, b"rectclip": rectclip, b"re": re_op, b"cm": lambda: concat_v(P.popn(6)), b"array": array_op, b"astore": astore,
            b"gsave": gsave, b"grestore": grestore, b"translate": translate, b"scale": scale, b"rotate": rotate, b"concat": concat,
            b"setlinewidth": setlinewidth, b"setlinejoin": setlinejoin, b"setlinecap": setlinecap, b"setdash": setdash,
            b"setrgbcolor": setrgb, b"setgray": lambda: (lambda g: setattr(P.gs, "fill", g) or setattr(P.gs, "stroke", g))(_gray(P.pop())),
            b"setcmykcolor": setcmyk, b"sethsbcolor": sethsb, b"setmiterlimit": lambda: P.pop(), b"setflat": lambda: P.pop(),
            b"showpage": lambda: None, b"initclip": lambda: None, b"setmatrix": lambda: P.pop(), b"matrix": lambda: P.stack.append([1, 0, 0, 1, 0, 0]),
            b"currentpoint": lambda: P.stack.extend(P.cur or (0, 0)), b"closepath_": closepath,
            # Illustrator's short names
            b"m": moveto, b"l": lineto, b"L": lineto, b"c": curveto, b"C": curveto, b"v": v_op, b"V": v_op, b"y": y_op, b"Y": y_op,
            b"h": closepath, b"H": lambda: None, b"n": lambda: P.paint(False, False), b"N": lambda: P.paint(False, False),
            b"f": fill, b"F": fill, b"s": lambda: P.paint(False, True, False, True), b"S": stroke,
            b"b": lambda: P.paint(True, True, False, True), b"B": lambda: P.paint(True, True),
            b"w": setlinewidth, b"j": setlinejoin, b"J": setlinecap, b"M": lambda: P.pop(), b"i": lambda: P.pop(), b"d": ill_ctm,
            b"g": gray_fill, b"G": gray_stroke, b"k": cmyk_fill, b"K": cmyk_stroke, b"x": custom_fill, b"X": custom_stroke,
            b"u": lambda: None, b"U": lambda: None, b"q": gsave, b"Q": grestore,
            b"*u": lambda: setattr(P, "compound", QPainterPath()),
            b"*U": self._end_compound,
            b"findfont": lambda: None, b"scalefont": lambda: P.pop(), b"setfont": lambda: P.pop(), b"show": lambda: P.pop(),
            b"currentfont": lambda: None, b"selectfont": lambda: (P.pop(), P.pop()), b"length": lambda: P.stack.append(0),
            b"dict": lambda: (P.pop(), P.stack.append({})), b"begin": lambda: P.pop(), b"end": lambda: None,
        }
        return o

    def _end_compound(self):
        c, paint = self.compound, self.comp_paint
        self.compound = None
        if c is not None and paint:
            self._emit(c, paint[0], paint[1], True)
        self.comp_paint = None

    # ---- run
    deadline = None
    progress = None

    def run(self, items):
        import time
        for t in items:
            if self.steps > 3_000_000:
                return
            self.steps += 1
            if (self.steps & 8191) == 0:
                if self.deadline and time.time() > self.deadline:
                    self.timed_out = True
                    self.steps = 3_000_001
                    return
                if self.progress:
                    self.progress(f"Reading EPS… {len(self.sink.items)} shapes")
            if isinstance(t, list):
                self.stack.append(t)
                continue
            self.exec_token(t)

    def exec_token(self, t):
        if not isinstance(t, bytes):                   # a value stored by def (number, array...)
            self.stack.append(t)
            return
        if t.startswith(b"/"):
            self.stack.append(t)                       # literal name (kept with its slash, def strips it)
            return
        if t in (b"<<", b">>"):
            self.ops[t]()
            return
        if t.startswith(b"(") or t.startswith(b"<"):
            self.stack.append(t)
            return
        try:
            self.stack.append(int(t))
            return
        except ValueError:
            pass
        try:
            self.stack.append(float(t))
            return
        except ValueError:
            pass
        if t in self.defs and t not in self.ops:
            v = self.defs[t]
            if isinstance(v, list) and getattr(v, "proc", True) and v and all(isinstance(x, (bytes, list)) for x in v):
                self.run(v)
            else:
                self.stack.append(v)
            return
        f = self.ops.get(t)
        if f is None:
            return                                     # an operator we do not know: ignored
        try:
            f()
        except (IndexError, TypeError, ValueError, ZeroDivisionError, OverflowError):
            pass


def _ps_color(vals):
    v = [float(x) for x in vals]
    return _gray(v[0]) if len(v) == 1 else _rgb(*v[:3]) if len(v) == 3 else _cmyk(*v[:4]) if len(v) >= 4 else "#000000"


def _ps_func_colors(f):
    if isinstance(f, list) and f:
        a, b = _ps_func_colors(f[0]), _ps_func_colors(f[-1])
        return (a[0], b[1]) if a and b else None
    if not isinstance(f, dict):
        return None
    ft = f.get(b"FunctionType")
    if ft == 2:
        return _ps_color(f.get(b"C0") or [0]), _ps_color(f.get(b"C1") or [1])
    if ft == 3:
        subs = f.get(b"Functions") or []
        a, b = (_ps_func_colors(subs[0]), _ps_func_colors(subs[-1])) if subs else (None, None)
        return (a[0], b[1]) if a and b else None
    return None


def _ps_shading(d, ctm):
    cols = _ps_func_colors(d.get(b"Function"))
    if not cols:
        return None
    typ = d.get(b"ShadingType")
    co = [float(x) for x in (d.get(b"Coords") or [])]
    angle = 0.0
    if typ == 2 and len(co) >= 4:
        a, b = ctm.map(QPointF(co[0], co[1])), ctm.map(QPointF(co[2], co[3]))
        if abs(b.x() - a.x()) + abs(b.y() - a.y()) > 1e-9:
            angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
    return {"type": "radial" if typ == 3 else "linear", "c1": cols[0], "c2": cols[1], "angle": round(angle, 2)}


def _strip_binary(data):
    """remove a DOS EPS header, pictures and binary blocks that would only confuse the reader"""
    if data[:4] == b"\xc5\xd0\xd3\xc6" and len(data) > 30:
        off = int.from_bytes(data[4:8], "little")
        ln = int.from_bytes(data[8:12], "little")
        data = data[off:off + ln]
    for tag in (b"%%BeginBinary", b"%%BeginData", b"%%BeginPhotoshop", b"%%BeginPreview", b"%ImageData", b"%%BeginDocument"):
        end = {b"%%BeginBinary": b"%%EndBinary", b"%%BeginData": b"%%EndData", b"%%BeginPhotoshop": b"%%EndPhotoshop",
               b"%%BeginPreview": b"%%EndPreview", b"%ImageData": b"%EndImageData", b"%%BeginDocument": b"%%EndDocument"}[tag]
        while True:
            i = data.find(tag)
            if i < 0:
                break
            j = data.find(end, i)
            data = data[:i] + (data[j + len(end):] if j >= 0 else b"")
    return data


def _bbox(data):
    m = re.search(rb"%%HiResBoundingBox:\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)", data)
    if not m:
        m = re.search(rb"%%BoundingBox:\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)", data)
    if m:
        x0, y0, x1, y1 = [float(v) for v in m.groups()]
        if x1 > x0 and y1 > y0:
            return x0, y0, x1, y1
    return 0.0, 0.0, 595.0, 842.0


def import_eps(path, progress=None):
    """-> (Page, [VItem], note)"""
    import time
    with open(path, "rb") as f:
        data = f.read()
    data = _strip_binary(data)
    x0, y0, x1, y1 = _bbox(data)
    base = QTransform(K, 0, 0, -K, -x0 * K, y1 * K)
    sink = Sink(base)
    # Illustrator files carry a long prolog of their own; the drawing starts after it. Other programs have a short one that only defines names.
    cut = 0
    for tag in (b"%%EndSetup", b"%%EndProlog"):
        i = data.find(tag)
        if i >= 0:
            cut = i + len(tag)
            break
    body = data[cut:] if (cut and b"Adobe_Illustrator" in data[:cut]) else data
    ps = PS(sink)
    ps.gs.ctm = QTransform(base)
    ps.deadline = time.time() + 40
    ps.progress = progress
    ps.timed_out = False
    toks = _parse(_tokens(body))
    ps.run(toks)
    items = sink.items
    page = Page(round((x1 - x0) * 25.4 / 72.0, 2), round((y1 - y0) * 25.4 / 72.0, 2))
    notes = []
    if ps.timed_out or ps.steps > 3_000_000:
        notes.append("the file is very complex: only part of it was imported")
    if not items:
        notes.append("no shapes were found (the file may contain only pictures or text)")
    if len(items) > MAX_SHAPES:
        items = items[:MAX_SHAPES]
        notes.append(f"only the first {MAX_SHAPES} shapes were imported")
    return page, items, "; ".join(notes)


# ======================================================================== AI and PDF (PyMuPDF)
def _pdf_parse_obj(s):
    """a tiny reader for PDF object text: dict, array, name, number, reference, string -> python"""
    pos = [0]
    ws = " \t\r\n\x0c\x00"

    def skip():
        while pos[0] < len(s) and s[pos[0]] in ws:
            pos[0] += 1

    def value():
        skip()
        if s.startswith("<<", pos[0]):
            pos[0] += 2
            d = {}
            while True:
                skip()
                if s.startswith(">>", pos[0]):
                    pos[0] += 2
                    return d
                if pos[0] >= len(s):
                    return d
                k = value()
                v = value()
                if isinstance(k, str):
                    d[k] = v
        if s.startswith("[", pos[0]):
            pos[0] += 1
            a = []
            while True:
                skip()
                if s.startswith("]", pos[0]) or pos[0] >= len(s):
                    pos[0] += 1
                    return a
                a.append(value())
        if s.startswith("/", pos[0]):
            m = re.compile(r"/[^\s\[\]<>()/]*").match(s, pos[0])
            pos[0] = m.end()
            return m.group(0)
        if s.startswith("(", pos[0]):
            depth, i = 0, pos[0]
            while i < len(s):
                if s[i] == "\\":
                    i += 2
                    continue
                depth += s[i] == "("
                depth -= s[i] == ")"
                i += 1
                if depth == 0:
                    break
            pos[0] = i
            return s[:0]
        m = re.compile(r"(\d+)\s+(\d+)\s+R").match(s, pos[0])
        if m:
            pos[0] = m.end()
            return ("ref", int(m.group(1)))
        m = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)").match(s, pos[0])
        if m:
            pos[0] = m.end()
            t = m.group(0)
            return float(t) if "." in t else int(t)
        m = re.compile(r"[^\s\[\]<>()/]+").match(s, pos[0])
        if m:
            pos[0] = m.end()
            return {"true": True, "false": False}.get(m.group(0), m.group(0))
        pos[0] += 1
        return None
    return value()


class PDFReader:
    def __init__(self, doc, page):
        self.doc, self.page = doc, page
        self.cache = {}

    def obj(self, x):
        if isinstance(x, tuple) and x and x[0] == "ref":
            n = x[1]
            if n not in self.cache:
                try:
                    self.cache[n] = _pdf_parse_obj(self.doc.xref_object(n, compressed=False))
                except Exception:  # noqa: BLE001
                    self.cache[n] = None
            return self.cache[n]
        return x

    def get(self, d, key):
        return self.obj(d.get(key)) if isinstance(d, dict) else None

    def page_resources(self):
        try:
            xr = self.page.xref
            for _ in range(12):                              # the resources may belong to a parent page node
                k, v = self.doc.xref_get_key(xr, "Resources")
                if k == "xref":
                    return self.obj(("ref", int(v.split()[0]))) or {}
                if k == "dict":
                    return _pdf_parse_obj(v) or {}
                pk, pv = self.doc.xref_get_key(xr, "Parent")
                if pk != "xref":
                    break
                xr = int(pv.split()[0])
        except Exception:  # noqa: BLE001
            pass
        return {}

    def stream(self, ref):
        try:
            return self.doc.xref_stream(ref[1]) or b""
        except Exception:  # noqa: BLE001
            return b""

    # ---- colours and gradients
    def color(self, comps):
        n = len(comps)
        if n == 1:
            return _gray(comps[0])
        if n == 3:
            return _rgb(*comps)
        if n == 4:
            return _cmyk(*comps)
        return "#000000"

    def func_colors(self, fobj):
        """first and last colour of a (stitching or exponential) function"""
        f = self.obj(fobj)
        if isinstance(f, list):
            fs = [self.obj(x) for x in f]
            cols = [self.func_colors(x) for x in fs if x]
            if cols:
                return cols[0][0], cols[-1][1]
        if not isinstance(f, dict):
            return None
        ft = f.get("/FunctionType")
        if ft == 2:
            c0 = [float(x) for x in (f.get("/C0") or [0.0])]
            c1 = [float(x) for x in (f.get("/C1") or [1.0])]
            return self.color(c0), self.color(c1)
        if ft == 3:
            subs = [self.obj(x) for x in (f.get("/Functions") or [])]
            first = self.func_colors(subs[0]) if subs else None
            last = self.func_colors(subs[-1]) if subs else None
            if first and last:
                return first[0], last[1]
        return None

    def shading_fill(self, sh, ctm):
        """a shading dictionary -> Vector gradient dict (two colours + angle)"""
        sh = self.obj(sh)
        if not isinstance(sh, dict):
            return None
        cols = self.func_colors(sh.get("/Function"))
        if not cols:
            return None
        typ = sh.get("/ShadingType")
        coords = [float(x) for x in (sh.get("/Coords") or [])]
        angle = 0.0
        if typ == 2 and len(coords) >= 4:
            a = ctm.map(QPointF(coords[0], coords[1]))
            b = ctm.map(QPointF(coords[2], coords[3]))
            if abs(b.x() - a.x()) + abs(b.y() - a.y()) > 1e-9:
                angle = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
        return {"type": "radial" if typ == 3 else "linear", "c1": cols[0], "c2": cols[1], "angle": round(angle, 2)}


def _page_ops(data):
    toks = _parse(_tokens(data))
    return toks


class PDFInterp:
    """runs a PDF content stream (the vector part): paths, colours, patterns, shading fills, form XObjects"""

    def __init__(self, rd, sink):
        self.rd, self.sink = rd, sink
        self.st = []
        self.gs = State()
        self.path = QPainterPath()
        self.cur = None
        self.start = None
        self.pending_clip = None
        self.depth = 0
        self.skipped_text = False
        self.deadline = None

    def pt(self, x, y):
        return self.gs.ctm.map(QPointF(x, y))

    def scale_px(self):
        c = self.gs.ctm
        return math.sqrt(abs(c.m11() * c.m22() - c.m12() * c.m21())) or 1.0

    def run(self, data, res, base_ctm=None):
        if self.depth > 8:
            return
        stack = []
        toks = _tokens(data)
        i, n = 0, len(toks)
        inline = False
        import time
        while i < n:
            if self.deadline and (i & 4095) == 0 and time.time() > self.deadline:
                return
            t = toks[i]
            i += 1
            if t == b"BI":                              # inline picture: skip to EI
                while i < n and toks[i] != b"EI":
                    i += 1
                i += 1
                stack.clear()
                continue
            if t == b"[":
                arr = []
                while i < n and toks[i] != b"]":
                    arr.append(self._val(toks[i]))
                    i += 1
                i += 1
                stack.append(arr)
                continue
            if t == b"<<":
                depth = 1
                while i < n and depth:
                    depth += toks[i] == b"<<"
                    depth -= toks[i] == b">>"
                    i += 1
                stack.append({})
                continue
            v = self._val(t)
            if v is not None:
                stack.append(v)
                continue
            self.op(t, stack, res)
            stack.clear()

    def _val(self, t):
        if t.startswith(b"/"):
            return t.decode("latin-1")
        if t.startswith(b"(") or t.startswith(b"<"):
            return b""
        try:
            return float(t)
        except ValueError:
            return None

    def op(self, o, a, res):
        g = self.gs
        try:
            if o == b"q":
                self.st.append(g.copy())
            elif o == b"Q":
                if self.st:
                    self.gs = self.st.pop()
            elif o == b"cm" and len(a) >= 6:
                g.ctm = QTransform(*a[-6:]) * g.ctm
            elif o == b"m":
                self.path.moveTo(self.pt(a[-2], a[-1]))
                self.cur = self.start = (a[-2], a[-1])
            elif o == b"l":
                if self.cur is None:
                    self.path.moveTo(self.pt(a[-2], a[-1]))
                    self.start = (a[-2], a[-1])
                else:
                    self.path.lineTo(self.pt(a[-2], a[-1]))
                self.cur = (a[-2], a[-1])
            elif o == b"c":
                self.path.cubicTo(self.pt(a[-6], a[-5]), self.pt(a[-4], a[-3]), self.pt(a[-2], a[-1]))
                self.cur = (a[-2], a[-1])
            elif o == b"v":
                c = self.cur or (a[-4], a[-3])
                self.path.cubicTo(self.pt(*c), self.pt(a[-4], a[-3]), self.pt(a[-2], a[-1]))
                self.cur = (a[-2], a[-1])
            elif o == b"y":
                self.path.cubicTo(self.pt(a[-4], a[-3]), self.pt(a[-2], a[-1]), self.pt(a[-2], a[-1]))
                self.cur = (a[-2], a[-1])
            elif o == b"h":
                if self.start is not None:
                    self.path.closeSubpath()
                    self.cur = self.start
            elif o == b"re":
                x, y, w, h = a[-4:]
                self.path.moveTo(self.pt(x, y))
                self.path.lineTo(self.pt(x + w, y))
                self.path.lineTo(self.pt(x + w, y + h))
                self.path.lineTo(self.pt(x, y + h))
                self.path.closeSubpath()
                self.cur = self.start = (x, y)
            elif o in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"S", b"s", b"n"):
                if o in (b"b", b"b*", b"s") and self.start is not None:
                    self.path.closeSubpath()
                fill = o in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*")
                strk = o in (b"B", b"B*", b"b", b"b*", b"S", b"s")
                even = o.endswith(b"*")
                if fill or strk:
                    self.sink.add(QPainterPath(self.path), g.fill if fill else None, g.stroke if strk else None, g.width, g.join, g.cap, g.dash,
                                  even, g.alpha if not fill else g.alpha, self.scale_px())
                if self.pending_clip is not None:
                    self.gs.clip = self.pending_clip(self.path) if callable(self.pending_clip) else None
                    self.pending_clip = None
                self.path = QPainterPath()
                self.cur = self.start = None
            elif o in (b"W", b"W*"):
                self.pending_clip = lambda p, even=(o == b"W*"): self._intersect(p, even)
            elif o == b"w":
                g.width = abs(a[-1])
            elif o == b"J":
                g.cap = ("flat", "round", "square")[int(a[-1]) % 3]
            elif o == b"j":
                g.join = ("miter", "round", "bevel")[int(a[-1]) % 3]
            elif o == b"d":
                arr = a[-2] if len(a) >= 2 and isinstance(a[-2], list) else (a[0] if a and isinstance(a[0], list) else [])
                g.dash = _dash_ok([float(x) for x in arr])
            elif o == b"gs":
                d = self.rd.get(self.rd.get(res, "/ExtGState"), a[-1]) if isinstance(res, dict) else None
                if isinstance(d, dict):
                    if isinstance(d.get("/ca"), (int, float)):
                        g.alpha = float(d["/ca"])
                    if isinstance(d.get("/LW"), (int, float)):
                        g.width = float(d["/LW"])
            elif o == b"g":
                g.fill = _gray(a[-1])
            elif o == b"G":
                g.stroke = _gray(a[-1])
            elif o == b"rg":
                g.fill = _rgb(*a[-3:])
            elif o == b"RG":
                g.stroke = _rgb(*a[-3:])
            elif o == b"k":
                g.fill = _cmyk(*a[-4:])
            elif o == b"K":
                g.stroke = _cmyk(*a[-4:])
            elif o in (b"sc", b"scn", b"SC", b"SCN"):
                nums = [x for x in a if isinstance(x, float)]
                name = a[-1] if a and isinstance(a[-1], str) else None
                val = None
                if name:
                    pat = self.rd.get(self.rd.get(res, "/Pattern"), name) if isinstance(res, dict) else None
                    if isinstance(pat, dict) and pat.get("/PatternType") == 2:
                        pm = pat.get("/Matrix")
                        m = QTransform(*[float(x) for x in pm]) if isinstance(pm, list) and len(pm) == 6 else QTransform()
                        val = self.rd.shading_fill(pat.get("/Shading"), m * self.base_ctm())
                    if val is None:
                        val = "#808080"
                elif nums:
                    val = self.rd.color(nums)
                if val is not None:
                    if o in (b"sc", b"scn"):
                        g.fill = val
                    else:
                        g.stroke = val if isinstance(val, str) else (val.get("c1") if isinstance(val, dict) else "#000000")
            elif o == b"sh":
                sh = self.rd.get(self.rd.get(res, "/Shading"), a[-1]) if isinstance(res, dict) else None
                if sh is not None:
                    fill = self.rd.shading_fill(sh, self.gs.ctm)
                    if fill and g.clip is not None and not g.clip.isEmpty():
                        self.sink.add(QPainterPath(g.clip), fill, None, 0, "miter", "flat", None, False, g.alpha, 1.0)
            elif o == b"Do":
                xo = self.rd.get(self.rd.get(res, "/XObject"), a[-1]) if isinstance(res, dict) else None
                ref = self.rd.get(res, "/XObject").get(a[-1]) if isinstance(self.rd.get(res, "/XObject"), dict) else None
                if isinstance(xo, dict) and xo.get("/Subtype") == "/Form" and isinstance(ref, tuple):
                    self.st.append(g.copy())
                    mm = xo.get("/Matrix")
                    if isinstance(mm, list) and len(mm) == 6:
                        self.gs.ctm = QTransform(*[float(x) for x in mm]) * self.gs.ctm
                    r2 = self.rd.get(xo, "/Resources") or res
                    self.depth += 1
                    try:
                        self.run(self.rd.stream(ref), r2)
                    finally:
                        self.depth -= 1
                    if self.st:
                        self.gs = self.st.pop()
                elif isinstance(xo, dict):
                    self.sink.skipped += 1
            elif o in (b"BT", b"Tj", b"TJ"):
                self.skipped_text = True
        except (IndexError, TypeError, ValueError):
            pass

    def base_ctm(self):
        return self.gs.ctm

    def _intersect(self, p, even):
        np_ = QPainterPath(p)
        np_.setFillRule(Qt.OddEvenFill if even else Qt.WindingFill)
        return np_ if self.gs.clip is None else self.gs.clip.intersected(np_)


def _text_items(page, sink, base_k=K):
    """text of the page -> outlines (the closest installed font is used)"""
    out = []
    try:
        d = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return out
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                txt = sp.get("text", "")
                if not txt.strip():
                    continue
                name = re.sub(r"^[A-Z]{6}\+", "", sp.get("font", "Sans"))
                fam = re.split(r"[-,]", name)[0]
                fam = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", fam)
                flags = sp.get("flags", 0)
                f = QFont(fam)
                f.setPixelSize(max(1, int(round(sp["size"] * base_k))))
                f.setBold(bool(flags & 16) or "Bold" in name)
                f.setItalic(bool(flags & 2) or "Italic" in name or "Oblique" in name)
                p = QPainterPath()
                ox, oy = sp["origin"]
                p.addText(QPointF(ox * base_k, oy * base_k), f, txt)
                col = QColor(int(sp.get("color", 0)))
                it = VItem(p, {"type": "solid", "c1": col.name(), "c2": "#FFFFFF", "angle": 0}, dict(DEFAULT_STROKE, width=0, color="none"))
                out.append(it)
    return out


MAX_SHAPES = 60000


def _mu_path(items):
    """PyMuPDF drawing items (page points) -> QPainterPath in Vector pixels"""
    path = QPainterPath()
    cur = None
    for it in items:
        k = it[0]
        if k == "l":
            a, b = it[1], it[2]
            if cur is None or abs(cur[0] - a.x) > 1e-3 or abs(cur[1] - a.y) > 1e-3:
                path.moveTo(a.x * K, a.y * K)
            path.lineTo(b.x * K, b.y * K)
            cur = (b.x, b.y)
        elif k == "c":
            a, c1, c2, b = it[1], it[2], it[3], it[4]
            if cur is None or abs(cur[0] - a.x) > 1e-3 or abs(cur[1] - a.y) > 1e-3:
                path.moveTo(a.x * K, a.y * K)
            path.cubicTo(c1.x * K, c1.y * K, c2.x * K, c2.y * K, b.x * K, b.y * K)
            cur = (b.x, b.y)
        elif k == "re":
            r = it[1]
            path.addRect(QRectF(r.x0 * K, r.y0 * K, r.width * K, r.height * K))
            cur = None
        elif k == "qu":
            q = it[1]
            path.moveTo(q.ul.x * K, q.ul.y * K)
            for pnt in (q.ur, q.lr, q.ll):
                path.lineTo(pnt.x * K, pnt.y * K)
            path.closeSubpath()
            cur = None
    return path


def _dash_list(ds):
    if not ds or not isinstance(ds, str):
        return None
    m = re.match(r"\[([^\]]*)\]", ds.strip())
    if not m:
        return None
    try:
        return _dash_ok([float(x) for x in m.group(1).split()])
    except ValueError:
        return None


def _gradients(doc, page, base, deadline):
    """only the gradient fills (sh, shading patterns), read with the own reader; skipped for very big pages"""
    import time
    data = page.read_contents()
    if len(data) > 6_000_000:
        return [], "gradients were not read (the page is very big)"
    sink = Sink(base)
    sink.only_gradients = True
    rd = PDFReader(doc, page)
    interp = PDFInterp(rd, sink)
    interp.gs.ctm = QTransform(base)
    interp.deadline = deadline
    interp.run(data, rd.page_resources())
    return sink.items, ("some gradients were skipped (the file is very complex)" if time.time() > deadline else "")


def import_pdf(path, page_no=0, progress=None):
    """.ai and .pdf -> (Page, [VItem], note). Shapes come from MuPDF (fast, C), gradients from the own reader."""
    import time
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(path)
    try:
        if doc.page_count == 0:
            raise ValueError("this file has no pages")
        page = doc[min(page_no, doc.page_count - 1)]
        if progress:
            progress("Reading shapes…")
        drawings = page.get_drawings(extended=True)
        notes = []
        sink = Sink(QTransform())
        clip_slots = []                                  # (index in sink.items, scissor rect in points)
        for n, d in enumerate(drawings):
            if progress and n % 2000 == 0:
                progress(f"Shapes {n} of {len(drawings)}…")
            t = d.get("type")
            if t == "clip":
                sc = d.get("scissor")
                if sc is not None:
                    clip_slots.append((len(sink.items), sc))
                continue
            if t not in ("f", "s", "fs"):
                continue
            if len(sink.items) >= MAX_SHAPES:
                notes.append(f"only the first {MAX_SHAPES} shapes were imported")
                break
            path = _mu_path(d.get("items") or [])
            if d.get("closePath"):
                path.closeSubpath()
            fill = _rgb(*d["fill"][:3]) if ("f" in t and d.get("fill") and len(d["fill"]) >= 3) else None
            stroke = _rgb(*d["color"][:3]) if ("s" in t and d.get("color") and len(d["color"]) >= 3) else None
            cap = d.get("lineCap")
            cap = cap[0] if isinstance(cap, (tuple, list)) and cap else (cap or 0)
            join = int(d.get("lineJoin") or 0)
            alpha = d.get("fill_opacity") if fill else d.get("stroke_opacity")
            sink.add(path, fill, stroke, float(d.get("width") or 1.0), ("miter", "round", "bevel")[join % 3],
                     ("flat", "round", "square")[int(cap) % 3], _dash_list(d.get("dashes")), bool(d.get("even_odd")),
                     1.0 if alpha is None else float(alpha), K)
        items = sink.items
        # gradients: put each one where MuPDF saw its clip
        rd = PDFReader(doc, page)
        res = rd.page_resources()
        raw = page.read_contents()
        if (b" sh" in raw or b"\nsh" in raw or b"/Pattern" in raw or b"scn" in raw) and len(items) < MAX_SHAPES:
            if progress:
                progress("Reading gradients…")
            m = page.transformation_matrix
            base = QTransform(m.a * K, m.b * K, m.c * K, m.d * K, m.e * K, m.f * K)
            grads, gnote = _gradients(doc, page, base, time.time() + 12)
            if gnote:
                notes.append(gnote)
            used = set()
            inserts = []
            for g in grads:
                r = g.path().boundingRect()
                best = None
                for k, (pos, sc) in enumerate(clip_slots):
                    if k in used:
                        continue
                    if (abs(sc.x0 * K - r.left()) < 3 and abs(sc.y0 * K - r.top()) < 3 and
                            abs(sc.x1 * K - r.right()) < 3 and abs(sc.y1 * K - r.bottom()) < 3):
                        best = k
                        break
                if best is None:
                    inserts.append((0, g))                  # not found: put it at the back
                else:
                    used.add(best)
                    inserts.append((clip_slots[best][0], g))
            for pos, g in sorted(inserts, key=lambda x: x[0], reverse=True):
                items.insert(pos, g)
        if progress:
            progress("Reading text…")
        items += _text_items(page, sink)
        r = page.rect
        pg = Page(round(r.width * 25.4 / 72.0, 2), round(r.height * 25.4 / 72.0, 2))
        nimg = len(page.get_images(full=True))
        if nimg:
            notes.append(f"{nimg} picture(s) in the file were not imported")
        if doc.page_count > 1:
            notes.append(f"only page 1 of {doc.page_count} was imported")
        if not items:
            notes.append("no vector shapes were found (the file may contain only pictures)")
        return pg, items, "; ".join(notes)
    finally:
        doc.close()


# ======================================================================== one entry for the program
def import_file(path, scene, progress=None):
    """.ai .pdf .eps -> shapes added to the scene. -> (Page, count, note)"""
    with open(path, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" in head[:1024]:                       # .pdf, and every .ai made since Illustrator 9 (PDF compatible)
        pg, items, note = import_pdf(path, progress=progress)
    else:                                             # .eps, and older .ai (PostScript)
        pg, items, note = import_eps(path, progress=progress)
    if progress:
        progress(f"Placing {len(items)} shapes…")
    scene.blockSignals(True)
    try:
        for k, it in enumerate(items):
            scene.addItem(it)
            if progress and k % 3000 == 2999:
                progress(f"Placing shapes… {k + 1} of {len(items)}")
    finally:
        scene.blockSignals(False)
    return pg, len(items), note
