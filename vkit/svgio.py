"""SVG export (clean, editable in other programs) and a practical SVG importer (paths, basic shapes, colours, transforms)."""
import math
import re
import xml.etree.ElementTree as ET

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QTransform

from .model import DASHES, DEFAULT_FILL, DEFAULT_STROKE, PX_PER_MM, Page, VGroup, VItem, leaves_of, path_to_json


def _d(path):
    out = []
    n, i = path.elementCount(), 0
    last = None
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.MoveToElement:
            out.append(f"M{e.x:.3f} {e.y:.3f}"); last = (e.x, e.y); start = (e.x, e.y)
        elif e.type == QPainterPath.LineToElement:
            out.append(f"L{e.x:.3f} {e.y:.3f}")
        else:
            a, b = path.elementAt(i + 1), path.elementAt(i + 2)
            out.append(f"C{e.x:.3f} {e.y:.3f} {a.x:.3f} {a.y:.3f} {b.x:.3f} {b.y:.3f}"); i += 2
        i += 1
    return " ".join(out)


def _closed_d(path):
    """add Z to subpaths whose last point returns to the first"""
    s = _d(path)
    parts = re.split(r"(?=M)", s)
    res = []
    for part in parts:
        if not part.strip():
            continue
        nums = re.findall(r"-?\d+\.?\d*", part)
        if len(nums) >= 6 and abs(float(nums[0]) - float(nums[-2])) < 1e-3 and abs(float(nums[1]) - float(nums[-1])) < 1e-3:
            part = part.rstrip() + " Z"
        res.append(part.strip())
    return " ".join(res)


INK = "http://www.inkscape.org/namespaces/inkscape"
SODI = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"


def _esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _text_origin(it):
    """for a text object whose outline is still the text: (x, y of the first baseline) - else None"""
    from .tools import text_path
    m = it.meta or {}
    if it.kind != "text" or not m.get("text") or m.get("on_path"):      # text along a path is written as its outlines
        return None
    ref = text_path(m)
    rb, cb = ref.boundingRect(), it.path().boundingRect()
    if abs(rb.width() - cb.width()) > 1.0 or abs(rb.height() - cb.height()) > 1.0:
        return None                                   # the letters were reshaped by hand: keep the outline
    o = cb.topLeft() - rb.topLeft()
    return o.x(), o.y()


def _matrix(t):
    if t.isIdentity():
        return ""
    return f' transform="matrix({t.m11():.6f} {t.m12():.6f} {t.m21():.6f} {t.m22():.6f} {t.dx():.3f} {t.dy():.3f})"'


def export_svg(scene, page, items=None, layers=None):
    """layers (Vector's list, bottom first) -> real layers in Inkscape and Illustrator.
    Text objects are written as editable text (with their font), the rest as paths."""
    defs, body = [], []
    gid = [0]

    def paint(it):
        f, s = it.fill, it.stroke
        fill = "none"
        if f["type"] == "solid":
            fill = f["c1"]
        elif f["type"] in ("linear", "radial"):
            gid[0] += 1
            name = f"g{gid[0]}"
            r = it.path().boundingRect()
            if f["type"] == "linear":
                a = math.radians(f.get("angle", 0)); c = r.center()
                half = (abs(r.width() * math.cos(a)) + abs(r.height() * math.sin(a))) / 2 or 1
                defs.append(f'<linearGradient id="{name}" gradientUnits="userSpaceOnUse" x1="{c.x() - half * math.cos(a):.2f}" y1="{c.y() - half * math.sin(a):.2f}" '
                            f'x2="{c.x() + half * math.cos(a):.2f}" y2="{c.y() + half * math.sin(a):.2f}"><stop offset="0" stop-color="{f["c1"]}"/><stop offset="1" stop-color="{f["c2"]}"/></linearGradient>')
            else:
                c = r.center()
                defs.append(f'<radialGradient id="{name}" gradientUnits="userSpaceOnUse" cx="{c.x():.2f}" cy="{c.y():.2f}" r="{max(r.width(), r.height()) / 2 or 1:.2f}">'
                            f'<stop offset="0" stop-color="{f["c1"]}"/><stop offset="1" stop-color="{f["c2"]}"/></radialGradient>')
            fill = f"url(#{name})"
        st = ""
        if s["width"] > 0 and s["color"] not in (None, "none"):
            st = f' stroke="{s["color"]}" stroke-width="{s["width"]:.3f}" stroke-linejoin="{s["join"]}" stroke-linecap="{ {"flat": "butt"}.get(s["cap"], s["cap"]) }"'
            d = DASHES.get(s["dash"])
            if d:
                st += f' stroke-dasharray="{" ".join(str(x * max(1, s["width"])) for x in d)}"'
        return fill, st

    def emit(it, ind="    "):
        if it.hidden:
            return
        op = f' opacity="{it.opacity():.3f}"' if it.opacity() < 0.999 else ""
        nm = f' inkscape:label="{_esc(it.name)}"' if getattr(it, "name", "") else ""
        if isinstance(it, VGroup):
            body.append(f"{ind}<g{nm}{op}>")
            for c in it.childItems():
                emit(c, ind + "  ")
            body.append(f"{ind}</g>")
            return
        fill, st = paint(it)
        t = it.sceneTransform()
        org = _text_origin(it)
        if org is not None:                                    # editable text
            m = it.meta
            from .tools import QFontMetricsF_height, QFont
            f = QFont(m.get("family", "Sans Serif"))
            f.setPixelSize(max(1, int(round(m.get("size", 48)))))
            lh = QFontMetricsF_height(f)
            style = (f"font-family:'{_esc(m.get('family', 'Sans Serif'))}';font-size:{max(1, int(round(m.get('size', 48))))}px;"
                     f"font-weight:{'bold' if m.get('bold') else 'normal'};font-style:{'italic' if m.get('italic') else 'normal'};"
                     f"line-height:{lh:.2f}px")
            spans = "".join(f'<tspan sodipodi:role="line" x="{org[0]:.3f}" y="{org[1] + k * lh:.3f}">{_esc(line) or " "}</tspan>'
                            for k, line in enumerate(m["text"].split("\n")))
            if m.get("angle"):
                r = QTransform(); r.translate(org[0], org[1]); r.rotate(m["angle"]); r.translate(-org[0], -org[1])
                t = r * t
            body.append(f'{ind}<text xml:space="preserve"{nm} style="{style}" fill="{fill}"{st}{op}{_matrix(t)}>{spans}</text>')
            return
        rule = ' fill-rule="evenodd"' if it.path().fillRule() == Qt.OddEvenFill else ""
        body.append(f'{ind}<path{nm} d="{_closed_d(it.path())}" fill="{fill}"{rule}{st}{op}{_matrix(t)}/>')

    top = items if items is not None else [i for i in scene.items(order=Qt.AscendingOrder) if i.group() is None and isinstance(i, (VItem, VGroup))]
    if layers and items is None:
        ids = [l["id"] for l in layers]
        for k, lay in enumerate(layers):
            mine = [i for i in top if (i.layer if i.layer in ids else ids[0]) == lay["id"]]
            lid = re.sub(r"[^A-Za-z0-9_-]", "_", lay["name"]) or f"layer{k + 1}"
            attrs = (f'id="{_esc(lid)}" data-name="{_esc(lay["name"])}" inkscape:groupmode="layer" inkscape:label="{_esc(lay["name"])}"'
                     + ("" if lay.get("visible", True) else ' style="display:none"')
                     + (' sodipodi:insensitive="true"' if lay.get("locked") else ""))
            body.append(f"  <g {attrs}>")
            for it in mine:
                emit(it)
            body.append("  </g>")
    else:
        for it in top:
            if it.isVisible():
                emit(it, "  ")
    w, h = page.w_mm, page.h_mm
    ox, oy = page.rect.x(), page.rect.y()                    # the active artboard
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="{INK}" xmlns:sodipodi="{SODI}" '
            f'width="{w:g}mm" height="{h:g}mm" viewBox="{ox:.3f} {oy:.3f} {w * PX_PER_MM:.3f} {h * PX_PER_MM:.3f}">\n'
            + ("<defs>" + "".join(defs) + "</defs>\n" if defs else "")
            + "\n".join(body) + "\n</svg>\n")


# ---------------------------------------------------------------- import
_num = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
NAMED = {"none": None}


def _color(v):
    if v is None:
        return None
    v = v.strip()
    if v in ("none", "transparent"):
        return None
    if v.startswith("url("):
        return v
    if v.startswith("rgb"):
        n = [float(x.strip("% ")) * (2.55 if "%" in x else 1) for x in re.findall(r"[\d.]+%?", v)[:3]]
        return QColor(int(n[0]), int(n[1]), int(n[2])).name()
    c = QColor(v)
    return c.name() if c.isValid() else "#000000"


def _arc(path, x1, y1, rx, ry, phi, fa, fs, x2, y2):
    """SVG elliptical arc -> cubic segments"""
    if rx == 0 or ry == 0:
        path.lineTo(x2, y2); return
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi); cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx *= math.sqrt(lam); ry *= math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(0, num / den)) if den else 0
    if fa == fs:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x1 + x2) / 2, sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2))))
    d = dt / n
    k = 4 / 3 * math.tan(d / 4)
    for i in range(n):
        a1, a2 = t1 + i * d, t1 + (i + 1) * d
        def pt(a, dx_=0, dy_=0):
            x, y = rx * math.cos(a) + dx_, ry * math.sin(a) + dy_
            return QPointF(cp * x - sp * y + cx, sp * x + cp * y + cy)
        p1 = pt(a1, -k * rx * math.sin(a1), k * ry * math.cos(a1))
        p2 = pt(a2, k * rx * math.sin(a2), -k * ry * math.cos(a2))
        p3 = pt(a2)
        path.cubicTo(p1, p2, p3)


def parse_path(d):
    path = QPainterPath()
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + _num.pattern, d)
    i, cmd = 0, None
    cur = QPointF(0, 0); start = QPointF(0, 0); lastc = None; lastq = None

    def nums(n):
        nonlocal i
        v = [float(toks[i + k]) for k in range(n)]
        i += n
        return v
    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]; i += 1
            if cmd in "Zz":
                path.closeSubpath(); cur = QPointF(start); lastc = lastq = None
                continue
        c = cmd
        rel = c.islower()
        u = c.upper()
        try:
            if u == "M":
                x, y = nums(2)
                cur = QPointF(cur.x() + x, cur.y() + y) if rel else QPointF(x, y)
                path.moveTo(cur); start = QPointF(cur); cmd = "l" if rel else "L"; lastc = lastq = None
            elif u == "L":
                x, y = nums(2)
                cur = QPointF(cur.x() + x, cur.y() + y) if rel else QPointF(x, y)
                path.lineTo(cur); lastc = lastq = None
            elif u == "H":
                (x,) = nums(1)
                cur = QPointF(cur.x() + x if rel else x, cur.y()); path.lineTo(cur); lastc = lastq = None
            elif u == "V":
                (y,) = nums(1)
                cur = QPointF(cur.x(), cur.y() + y if rel else y); path.lineTo(cur); lastc = lastq = None
            elif u == "C":
                a = nums(6)
                o = cur if rel else QPointF(0, 0)
                c1, c2, p = QPointF(o.x() + a[0], o.y() + a[1]), QPointF(o.x() + a[2], o.y() + a[3]), QPointF(o.x() + a[4], o.y() + a[5])
                path.cubicTo(c1, c2, p); cur = p; lastc = c2; lastq = None
            elif u == "S":
                a = nums(4)
                o = cur if rel else QPointF(0, 0)
                c1 = QPointF(2 * cur.x() - lastc.x(), 2 * cur.y() - lastc.y()) if lastc else QPointF(cur)
                c2, p = QPointF(o.x() + a[0], o.y() + a[1]), QPointF(o.x() + a[2], o.y() + a[3])
                path.cubicTo(c1, c2, p); cur = p; lastc = c2; lastq = None
            elif u == "Q":
                a = nums(4)
                o = cur if rel else QPointF(0, 0)
                q, p = QPointF(o.x() + a[0], o.y() + a[1]), QPointF(o.x() + a[2], o.y() + a[3])
                path.quadTo(q, p); cur = p; lastq = q; lastc = None
            elif u == "T":
                a = nums(2)
                o = cur if rel else QPointF(0, 0)
                q = QPointF(2 * cur.x() - lastq.x(), 2 * cur.y() - lastq.y()) if lastq else QPointF(cur)
                p = QPointF(o.x() + a[0], o.y() + a[1])
                path.quadTo(q, p); cur = p; lastq = q; lastc = None
            elif u == "A":
                a = nums(7)
                p = QPointF(cur.x() + a[5], cur.y() + a[6]) if rel else QPointF(a[5], a[6])
                _arc(path, cur.x(), cur.y(), a[0], a[1], a[2], int(a[3]), int(a[4]), p.x(), p.y()); cur = p; lastc = lastq = None
            else:
                break
        except (IndexError, ValueError):
            break
    return path


def _transform(v):
    t = QTransform()
    if not v:
        return t
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", v):
        a = [float(x) for x in _num.findall(args)]
        m = QTransform()
        if name == "translate":
            m.translate(a[0], a[1] if len(a) > 1 else 0)
        elif name == "scale":
            m.scale(a[0], a[1] if len(a) > 1 else a[0])
        elif name == "rotate":
            if len(a) == 3:
                m.translate(a[1], a[2]); m.rotate(a[0]); m.translate(-a[1], -a[2])
            else:
                m.rotate(a[0])
        elif name == "matrix" and len(a) == 6:
            m = QTransform(a[0], a[1], a[2], a[3], a[4], a[5])
        elif name == "skewX":
            m = QTransform(1, 0, math.tan(math.radians(a[0])), 1, 0, 0)
        elif name == "skewY":
            m = QTransform(1, math.tan(math.radians(a[0])), 0, 1, 0, 0)
        t = m * t            # list order: the last transform is applied to the point first
    return t


def _len(v, default=0.0):
    if v is None:
        return default
    m = _num.search(v)
    if not m:
        return default
    x = float(m.group())
    u = v[m.end():].strip()
    return x * {"mm": PX_PER_MM, "cm": PX_PER_MM * 10, "in": 96.0, "pt": 96 / 72.0, "pc": 16.0}.get(u, 1.0)


_CSS = {}                                  # class name -> {property: value}  (Illustrator writes its colours this way)
_PROPS = ("fill", "stroke", "stroke-width", "opacity", "fill-rule", "stroke-linejoin", "stroke-linecap", "stroke-dasharray",
          "font-family", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline")
_NOT_INHERITED = ()


def _read_css(root):
    _CSS.clear()
    for el in root.iter():
        if el.tag.split("}")[-1] != "style":
            continue
        css = re.sub(r"/\*.*?\*/", "", "".join(el.itertext()), flags=re.S)
        for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            props = {}
            for part in body.split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    props[k.strip()] = v.strip()
            for one in sel.split(","):
                one = one.strip()
                if one.startswith("."):
                    _CSS.setdefault(one[1:], {}).update(props)


def _style(el, inherited):
    st = {k: v for k, v in inherited.items() if k not in _NOT_INHERITED}
    for k in _PROPS:
        if el.get(k) is not None:
            st[k] = el.get(k)
    for c in (el.get("class") or "").split():
        st.update(_CSS.get(c, {}))
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    return st


def _attr(el, name):
    """an attribute with or without its namespace (inkscape:label, sodipodi:insensitive, xlink:href ...)"""
    v = el.get(name)
    if v is not None:
        return v
    for k, val in el.attrib.items():
        if k.split("}")[-1] == name:
            return val
    return None


def _hidden(el):
    if el.get("display") == "none" or el.get("visibility") == "hidden":
        return True
    return bool(re.search(r"(^|;)\s*display\s*:\s*none", el.get("style") or ""))


def _family(v):
    """CSS font-family list -> (installed family if any, bold, italic). Illustrator writes e.g. MyriadPro-Bold, 'Myriad Pro'"""
    from PySide6.QtGui import QFontDatabase
    names = [n.strip().strip("'\"") for n in v.split(",") if n.strip()]
    fams = {re.sub(r"[^a-z0-9]", "", f.lower()): f for f in QFontDatabase.families()}
    bold = italic = False
    for n in names:
        base, _, style = n.partition("-")
        st = style.lower()
        bold = bold or any(k in st for k in ("bold", "black", "heavy", "semibold"))
        italic = italic or any(k in st for k in ("italic", "oblique"))
        for cand in (n, base, re.sub(r"(MT|PS|Std|Pro)$", "", base)):
            key = re.sub(r"[^a-z0-9]", "", cand.lower())
            if key in fams:
                return fams[key], bold, italic
    plain = [n for n in names if "-" not in n and not re.search(r"(MT|PS)$", n)]
    first = plain[0] if plain else (names[0] if names else "Sans Serif")
    first = first.partition("-")[0] if "-" in first and " " not in first else first
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", first), bold, italic


def _anchor_shift(meta, size, pos, st):
    """text-anchor (start / middle / end) and dominant-baseline (central, middle, hanging ...) -> the baseline start point"""
    from PySide6.QtGui import QFont, QFontMetricsF
    anchor = (st.get("text-anchor") or "start").strip()
    base = (st.get("dominant-baseline") or "auto").strip()
    if anchor in ("start", "inherit") and base in ("auto", "alphabetic", "inherit"):
        return pos
    f = QFont(meta["family"]); f.setPixelSize(max(1, int(round(size))))
    f.setBold(meta["bold"]); f.setItalic(meta["italic"])
    fm = QFontMetricsF(f)
    x, y = pos
    w = max(fm.horizontalAdvance(line) for line in meta["text"].split("\n"))
    if anchor == "middle":
        x -= w / 2
    elif anchor == "end":
        x -= w
    if base in ("central", "mathematical"):
        y += (fm.ascent() - fm.descent()) / 2
    elif base == "middle":
        y += fm.xHeight() / 2
    elif base in ("hanging", "text-before-edge"):
        y += fm.ascent()
    elif base in ("text-after-edge", "ideographic"):
        y -= fm.descent()
    return x, y


def _is_page_background(el, vb):
    """a plain white rectangle that fills the whole page: the page itself, not a shape to click on"""
    if el.tag.split("}")[-1] != "rect" or vb is None:
        return False
    st = _style(el, {})
    if (_color(st.get("fill", "#000000")) or "").lower() not in ("#ffffff", "#fff", "white") or st.get("stroke") not in (None, "none"):
        return False
    vx, vy, vw, vh = vb
    x, y, w, h = _len(el.get("x")), _len(el.get("y")), _len(el.get("width")), _len(el.get("height"))
    return x <= vx + 0.5 and y <= vy + 0.5 and x + w >= vx + vw - 0.5 and y + h >= vy + vh - 0.5


def import_svg(text, scene, with_layers=False):
    """-> (Page, number of shapes) or, with_layers, (Page, number, layers). Shapes are added to scene.
    Inkscape and Illustrator layers become Vector layers; <text> becomes editable Vector text."""
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        # a file with prefixes that are used but never declared: drop the prefixes and try once more
        t2 = re.sub(r"\s(\w+):(\w[\w-]*)=", r" \1_\2=", re.sub(r"\sxmlns(:\w+)?=\"[^\"]*\"", "", text))
        t2 = re.sub(r"<(/?)\w+:", r"<\1", t2)
        root = ET.fromstring(t2)
    _read_css(root)
    vb = root.get("viewBox")
    if vb:
        vx, vy, vw, vh = [float(x) for x in _num.findall(vb)]
    else:
        vx = vy = 0
        vw, vh = _len(root.get("width"), 800), _len(root.get("height"), 600)
    pw, ph = _len(root.get("width"), vw), _len(root.get("height"), vh)
    sx, sy = (pw / vw if vw else 1), (ph / vh if vh else 1)
    base = QTransform().scale(sx, sy).translate(-vx, -vy)
    page = Page(round(pw / PX_PER_MM, 2), round(ph / PX_PER_MM, 2))
    grads = {}
    for g in root.iter():
        tag = g.tag.split("}")[-1]
        if tag in ("linearGradient", "radialGradient") and g.get("id"):
            stops = [(s.get("offset", "0"), s.get("stop-color") or re.sub(r".*stop-color:\s*([^;]+).*", r"\1", s.get("style", "")) or "#000000")
                     for s in g if s.tag.split("}")[-1] == "stop"]
            href = (_attr(g, "href") or "").lstrip("#")
            grads[g.get("id")] = (tag, stops, href, g)
    count = [0]

    def fill_spec(v, bbox):
        if v is None:
            return {"type": "solid", "c1": "#000000", "c2": "#FFFFFF", "angle": 0}
        c = _color(v)
        if c is None:
            return {"type": "none", "c1": "#000000", "c2": "#FFFFFF", "angle": 0}
        if c.startswith("url("):
            gid = c[5:c.index(")")].strip("'\"")
            tag, stops, href, el = grads.get(gid, (None, [], "", None))
            if not stops and href in grads:
                stops = grads[href][1]
            if len(stops) >= 2:
                c1, c2 = _color(stops[0][1]) or "#000000", _color(stops[-1][1]) or "#FFFFFF"
                ang = 0.0
                if tag == "linearGradient" and el is not None:
                    def g(k, d):
                        val = el.get(k)
                        if val is None:
                            return d
                        return _len(val.replace("%", ""), d) / (100.0 if "%" in val else 1.0)
                    dx, dy = g("x2", 1.0) - g("x1", 0.0), g("y2", 0.0) - g("y1", 0.0)
                    if abs(dx) + abs(dy) > 1e-9:
                        ang = math.degrees(math.atan2(dy, dx))
                return {"type": "radial" if tag == "radialGradient" else "linear", "c1": c1, "c2": c2, "angle": round(ang, 2)}
            return {"type": "solid", "c1": "#808080", "c2": "#FFFFFF", "angle": 0}
        return {"type": "solid", "c1": c, "c2": "#FFFFFF", "angle": 0}

    def shape(el, st, T):
        tag = el.tag.split("}")[-1]
        p = None
        if tag == "path":
            p = parse_path(el.get("d", ""))
        elif tag == "rect":
            x, y, w, h = _len(el.get("x")), _len(el.get("y")), _len(el.get("width")), _len(el.get("height"))
            rx = _len(el.get("rx"), _len(el.get("ry")))
            p = QPainterPath(); p.addRoundedRect(QRectF(x, y, w, h), rx, rx) if rx else p.addRect(QRectF(x, y, w, h))
        elif tag in ("circle", "ellipse"):
            cx, cy = _len(el.get("cx")), _len(el.get("cy"))
            rx = _len(el.get("r")) if tag == "circle" else _len(el.get("rx"))
            ry = rx if tag == "circle" else _len(el.get("ry"))
            p = QPainterPath(); p.addEllipse(QPointF(cx, cy), rx, ry)
        elif tag == "line":
            p = QPainterPath(); p.moveTo(_len(el.get("x1")), _len(el.get("y1"))); p.lineTo(_len(el.get("x2")), _len(el.get("y2")))
            st = dict(st); st.setdefault("fill", "none"); st["fill"] = "none"
        elif tag in ("polygon", "polyline"):
            n = [float(x) for x in _num.findall(el.get("points", ""))]
            if len(n) >= 4:
                p = QPainterPath(); p.moveTo(n[0], n[1])
                for k in range(2, len(n) - 1, 2):
                    p.lineTo(n[k], n[k + 1])
                if tag == "polygon":
                    p.closeSubpath()
        if p is None or p.isEmpty():
            return None
        p = T.map(p)
        it = VItem(p, fill_spec(st.get("fill", "#000000"), None), dict(DEFAULT_STROKE))
        sc = (abs(T.m11()) + abs(T.m22())) / 2 if T else 1
        sw = _len(st.get("stroke-width"), 1.0) * sc
        scol = _color(st.get("stroke")) if st.get("stroke") else None
        if scol and not scol.startswith("url("):
            dash = "solid"
            da = st.get("stroke-dasharray")
            if da and da != "none":
                nums = [float(x) for x in _num.findall(da)]
                if nums and sum(nums) > 0:
                    dash = "dashdot" if len(nums) >= 4 else ("dot" if nums[0] <= 2 * max(sw, 1e-6) else "dash")
            it.stroke = {"color": scol, "width": sw, "dash": dash, "join": st.get("stroke-linejoin", "miter") if st.get("stroke-linejoin") in ("miter", "round", "bevel") else "miter",
                         "cap": {"butt": "flat"}.get(st.get("stroke-linecap"), st.get("stroke-linecap", "flat")) if st.get("stroke-linecap") in ("butt", "round", "square") else "flat"}
        else:
            it.stroke = dict(DEFAULT_STROKE); it.stroke["width"] = 0
        if st.get("fill-rule") == "evenodd":
            pp = it.path(); pp.setFillRule(Qt.OddEvenFill); it.set_path(pp)
        it.restyle()
        try:
            it.setOpacity(float(st.get("opacity", 1)))
        except ValueError:
            pass
        count[0] += 1
        return it

    def text_item(el, st, T):
        """<text> -> an editable Vector text object (font, size, bold, italic, colour)"""
        from .tools import text_path
        lines, pos = [], None
        spans = [c for c in el if c.tag.split("}")[-1] == "tspan"]
        if spans:
            last_y = None
            for sp in spans:
                t = "".join(sp.itertext())
                y = sp.get("y")
                if pos is None and sp.get("x") is not None:
                    pos = (_len(sp.get("x")), _len(y))
                new_line = (_attr(sp, "role") == "line" or not lines or
                            (y is not None and last_y is not None and abs(_len(y) - last_y) > 0.01))
                if new_line:
                    lines.append(t)
                else:
                    lines[-1] += t                         # pieces of one line (Illustrator splits lines for kerning)
                if y is not None:
                    last_y = _len(y)
        else:
            lines = ["".join(el.itertext())]
        if el.text and el.text.strip() and spans:
            lines[0] = el.text.strip() + lines[0]
        if pos is None:
            pos = (_len(el.get("x")), _len(el.get("y")))
        if not any(l.strip() for l in lines):
            return None
        st2 = dict(st)
        for sp in spans[:1]:
            st2 = _style(sp, st2)
        fam, fb, fi = _family(st2.get("font-family") or "Sans Serif")
        size = _len(st2.get("font-size"), 16.0)
        sc = math.sqrt(abs(T.m11() * T.m22() - T.m12() * T.m21())) or 1.0
        meta = {"text": "\n".join(lines), "family": fam, "size": max(1.0, size * sc),
                "bold": fb or (st2.get("font-weight") or "normal") in ("bold", "bolder", "600", "700", "800", "900"),
                "italic": fi or (st2.get("font-style") or "normal") in ("italic", "oblique")}
        rotated = abs(T.m12()) > 1e-6 or abs(T.m21()) > 1e-6 or T.m11() < 0 or T.m22() < 0
        pos = _anchor_shift(meta, size, pos, st2)
        o = T.map(QPointF(*pos))
        a, b, c, d = T.m11(), T.m12(), T.m21(), T.m22()
        tol = 1e-3 * max(abs(a), abs(b), abs(c), abs(d), 1e-9)
        if st2.get("text-anchor", "start").strip() in ("middle", "end"):
            meta["anchor"] = st2["text-anchor"].strip()          # editing keeps the text centred (or right-aligned)
        if rotated and abs(a - d) < tol and abs(b + c) < tol and a * d - b * c > 0:
            # turned (not mirrored or squashed): stays editable text with its angle
            meta["angle"] = round(math.degrees(math.atan2(b, a)) % 360, 4)
            rotated = False
        if rotated:
            p = T.map(text_path(dict(meta, size=size), QPointF(*pos)))
        else:
            p = text_path(meta, o)
        it = VItem(p, fill_spec(st2.get("fill", "#000000"), None), dict(DEFAULT_STROKE, width=0.0))
        if not rotated:
            it.kind = "text"
            it.meta = meta
        it.restyle()
        try:
            it.setOpacity(float(st2.get("opacity", 1)))
        except ValueError:
            pass
        count[0] += 1
        return it

    def walk(el, st, T, parent):
        tag = el.tag.split("}")[-1]
        if tag in ("defs", "clipPath", "mask", "symbol", "linearGradient", "radialGradient", "style", "title", "desc", "metadata",
                   "namedview", "pattern", "marker", "filter"):
            return
        if _hidden(el):
            return
        st2 = _style(el, st)
        T2 = _transform(el.get("transform")) * T if el.get("transform") else T
        if tag in ("g", "svg", "a", "switch"):
            holder = []
            for c in el:
                walk(c, st2, T2, holder)
            return holder_to_item(holder, parent, _attr(el, "label") or "")
        if tag == "text":
            it = text_item(el, st2, T2)
            if it is not None and _attr(el, "label"):
                it.name = _attr(el, "label")
        else:
            it = shape(el, st2, T2)
        if it is not None:
            if _attr(el, "label"):
                it.name = _attr(el, "label")
            parent.append(it)

    def holder_to_item(holder, parent, name=""):
        if not holder:
            return
        if len(holder) == 1:
            parent.append(holder[0]); return
        g = VGroup()
        for h in holder:
            g.addToGroup(h)
        if name:
            g.name = name
        parent.append(g)

    # layers: Inkscape (inkscape:groupmode="layer") or Illustrator (a top-level group with data-name, or id Layer_1 ...)
    def is_layer(el):
        if el.tag.split("}")[-1] != "g":
            return False
        if _attr(el, "groupmode") == "layer":
            return True
        return el.get("data-name") is not None or bool(re.match(r"^(Layer|Ebene|Calque|Livello|Capa)_\d+$", el.get("id") or ""))

    tops = [c for c in root if c.tag.split("}")[-1] not in ("defs", "namedview", "metadata", "title", "desc", "style")]
    layer_els = [c for c in tops if is_layer(c)]
    gs = [c for c in tops if c.tag.split("}")[-1] == "g"]
    tops = [c for c in tops if not _is_page_background(c, (vx, vy, vw, vh) if vb else None)]
    if len(layer_els) < len(gs) and len(gs) >= 2 and all(g.get("id") or _attr(g, "label") for g in gs):
        layer_els = gs                                  # Illustrator: every top-level group with a name is a layer
    layers, top = [], []
    if with_layers and layer_els and len(layer_els) == len(gs):
        st0 = {"fill": "#000000"}
        loose = []
        for c in tops:
            if c not in layer_els:
                walk(c, st0, base, loose)                     # shapes outside any layer: bottom layer
        for k, el in enumerate(layer_els):
            lid = f"L{k + 1}"
            name = _attr(el, "label") or el.get("data-name") or (el.get("id") or f"Layer {k + 1}").replace("_", " ")
            layers.append({"id": lid, "name": name, "visible": not _hidden(el), "locked": _attr(el, "insensitive") == "true"})
            st2 = _style(el, st0)
            T2 = _transform(el.get("transform")) * base if el.get("transform") else base
            mine = []
            hid = el.attrib.pop("style", None) if _hidden(el) else None   # a hidden layer is still imported (just hidden)
            for c in el:
                walk(c, st2, T2, mine)
            if hid is not None:
                el.set("style", hid)
            for it in (loose if k == 0 else []) + mine:
                it.layer = lid
                top.append(it)
    else:
        for c in tops:
            walk(c, {"fill": "#000000"}, base, top)
    for it in top:
        scene.addItem(it)
    if with_layers:
        return page, count[0], layers
    return page, count[0]
