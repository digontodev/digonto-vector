"""Image trace: a picture (logo, scan, drawing) becomes editable vector shapes. Pure Python + Qt, no extra libraries."""
import math
import random

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainterPath

from .nodes import Node, Sub, path_from_subs, simplify


def _loops(mask, w, h):
    """closed outlines (lists of (x, y) corner points) around the True cells of mask (a list of rows of 0/1)"""
    nxt = {}                      # start vertex -> list of end vertices (inside on the left)
    def add(a, b):
        nxt.setdefault(a, []).append(b)
    for y in range(h):
        row = mask[y]
        up = mask[y - 1] if y > 0 else None
        for x in range(w):
            if not row[x]:
                continue
            if up is None or not up[x]:
                add((x, y), (x + 1, y))            # top edge, going right
            if y == h - 1 or not mask[y + 1][x]:
                add((x + 1, y + 1), (x, y + 1))    # bottom edge, going left
            if x == 0 or not row[x - 1]:
                add((x, y + 1), (x, y))            # left edge, going up
            if x == w - 1 or not row[x + 1]:
                add((x + 1, y), (x + 1, y + 1))    # right edge, going down
    loops = []
    while nxt:
        start = next(iter(nxt))
        loop = [start]
        cur = start
        prev_dir = None
        while True:
            outs = nxt.get(cur)
            if not outs:
                break
            if len(outs) > 1 and prev_dir is not None:      # saddle: keep the two diagonal pixels apart (turn right)
                best = min(outs, key=lambda o: _turn(prev_dir, (o[0] - cur[0], o[1] - cur[1])))
                outs.remove(best)
                nx = best
            else:
                nx = outs.pop()
            if not outs:
                del nxt[cur]
            prev_dir = (nx[0] - cur[0], nx[1] - cur[1])
            cur = nx
            if cur == start:
                break
            loop.append(cur)
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def _turn(a, b):
    c = a[0] * b[1] - a[1] * b[0]           # >0 turn right (y down), 0 straight, <0 left
    return -c if c != 0 else 0.5


def _area(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return s / 2


def _drop_collinear(pts):
    out = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) != 0:
            out.append(b)
    return out


def _to_sub(loop, detail, smooth_corners):
    pts = [QPointF(x, y) for x, y in _drop_collinear(loop)]
    if len(pts) < 3:
        return None
    # centre of the staircase: shift half a step so curves follow the middle of the pixel edge
    pts = simplify(pts + [pts[0]], detail)[:-1]
    if len(pts) < 3:
        return None
    nodes = [Node(p) for p in pts]
    m = len(nodes)
    if smooth_corners:
        for i, n in enumerate(nodes):
            a, b = nodes[i - 1].p, nodes[(i + 1) % m].p
            v1 = (n.p.x() - a.x(), n.p.y() - a.y()); v2 = (b.x() - n.p.x(), b.y() - n.p.y())
            l1, l2 = math.hypot(*v1), math.hypot(*v2)
            if l1 < 1e-9 or l2 < 1e-9:
                continue
            cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
            if cosang > 0.5:                         # turn smaller than about 60 degrees: smooth node
                d = QPointF((b.x() - a.x()) / 6.0, (b.y() - a.y()) / 6.0)
                k1, k2 = min(1.0, l1 / (3 * math.hypot(d.x(), d.y()) + 1e-9)), min(1.0, l2 / (3 * math.hypot(d.x(), d.y()) + 1e-9))
                n.hin = n.p - d * k1
                n.hout = n.p + d * k2
                n.smooth = True
    return Sub(nodes, True)


def _kmeans(pixels, k, iters=8):
    cents = [list(pixels[0])]                          # farthest-first start: the colours begin far apart
    while len(cents) < min(k, len(pixels)):
        best, bd = pixels[0], -1
        for p in pixels:
            d = min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2 for c in cents)
            if d > bd:
                bd, best = d, p
        cents.append(list(best))
    for _ in range(iters):
        sums = [[0, 0, 0, 0] for _ in cents]
        for p in pixels:
            bi, bd = 0, 1e18
            for i, c in enumerate(cents):
                d = (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2
                if d < bd:
                    bd, bi = d, i
            s = sums[bi]
            s[0] += p[0]; s[1] += p[1]; s[2] += p[2]; s[3] += 1
        for i, s in enumerate(sums):
            if s[3]:
                cents[i] = [s[0] / s[3], s[1] / s[3], s[2] / s[3]]
    return cents


def trace_image(img, mode="bw", threshold=128, colors=4, detail=1.0, smooth=True, min_area=6, invert=False, max_side=360):
    """-> (list of (QPainterPath, '#rrggbb'), width_px, height_px) in the coordinates of the (downscaled) image"""
    if img.isNull():
        raise ValueError("could not read the image")
    sc = min(1.0, max_side / max(img.width(), img.height()))
    if sc < 1.0:
        img = img.scaled(max(1, round(img.width() * sc)), max(1, round(img.height() * sc)), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    px = [[img.pixelColor(x, y) for x in range(w)] for y in range(h)]
    layers = []
    if mode == "bw":
        mask = [[1 if (c.alpha() > 127 and ((0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() < threshold) != invert)) else 0 for c in row] for row in px]
        layers.append((mask, "#000000"))
    else:
        samples = [(c.red(), c.green(), c.blue()) for row in px[::2] for c in row[::2] if c.alpha() > 127]
        if not samples:
            raise ValueError("the image is empty")
        cents = _kmeans(samples[::max(1, len(samples) // 1500)], max(2, colors))
        label = [[-1] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                c = px[y][x]
                if c.alpha() <= 127:
                    continue
                bi, bd = 0, 1e18
                for i, ce in enumerate(cents):
                    d = (c.red() - ce[0]) ** 2 + (c.green() - ce[1]) ** 2 + (c.blue() - ce[2]) ** 2
                    if d < bd:
                        bd, bi = d, i
                label[y][x] = bi
        # the colour that fills most of the border is the background: skip it
        border = {}
        for x in range(w):
            for y in (0, h - 1):
                border[label[y][x]] = border.get(label[y][x], 0) + 1
        for y in range(h):
            for x in (0, w - 1):
                border[label[y][x]] = border.get(label[y][x], 0) + 1
        bg = max(border, key=border.get) if border else -1
        order = sorted(range(len(cents)), key=lambda i: -sum(row.count(i) for row in label))
        for i in order:
            if i == bg:
                continue
            mask = [[1 if v == i else 0 for v in row] for row in label]
            c = cents[i]
            layers.append((mask, "#%02X%02X%02X" % (round(c[0]), round(c[1]), round(c[2]))))
    out = []
    for mask, col in layers:
        subs = []
        for loop in _loops(mask, w, h):
            if abs(_area(loop)) < min_area:
                continue
            s = _to_sub(loop, 0.6 * detail, smooth)
            if s:
                subs.append(s)
        if subs:
            p = path_from_subs(subs, Qt.OddEvenFill)
            out.append((p, col))
    return out, w, h
