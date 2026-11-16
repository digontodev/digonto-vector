"""Editable node model: a path is a list of subpaths, each a list of nodes with optional bezier handles."""
import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

from .model import path_to_segs


class Node:
    __slots__ = ("p", "hin", "hout", "smooth")

    def __init__(self, p, hin=None, hout=None, smooth=False):
        self.p = QPointF(p)
        self.hin = QPointF(hin) if hin is not None else None
        self.hout = QPointF(hout) if hout is not None else None
        self.smooth = smooth

    def copy(self):
        return Node(self.p, self.hin, self.hout, self.smooth)


class Sub:
    def __init__(self, nodes=None, closed=False):
        self.nodes = nodes or []
        self.closed = closed


def _eq(a, b):
    return abs(a.x() - b.x()) < 1e-6 and abs(a.y() - b.y()) < 1e-6


def _colinear_smooth(n):
    if n.hin is None or n.hout is None:
        return False
    a = n.p - n.hin
    b = n.hout - n.p
    cross = a.x() * b.y() - a.y() * b.x()
    la, lb = math.hypot(a.x(), a.y()), math.hypot(b.x(), b.y())
    return la > 1e-6 and lb > 1e-6 and abs(cross) / (la * lb) < 0.02


def subs_from_path(path):
    subs, cur = [], None
    for s in path_to_segs(path):
        if s[0] == "M":
            cur = Sub([Node(s[1])])
            subs.append(cur)
        elif s[0] == "L":
            cur.nodes.append(Node(s[1]))
        elif s[0] == "C":
            cur.nodes[-1].hout = QPointF(s[1])
            n = Node(s[3], hin=s[2])
            cur.nodes.append(n)
    for sb in subs:
        if len(sb.nodes) > 2 and _eq(sb.nodes[0].p, sb.nodes[-1].p):
            last = sb.nodes.pop()
            sb.nodes[0].hin = last.hin
            sb.closed = True
        for n in sb.nodes:
            n.smooth = _colinear_smooth(n)
    return subs


def path_from_subs(subs, rule=None):
    p = QPainterPath()
    for sb in subs:
        ns = sb.nodes
        if not ns:
            continue
        p.moveTo(ns[0].p)
        count = len(ns) + (1 if sb.closed else 0)
        for i in range(1, count):
            a, b = ns[i - 1], ns[i % len(ns)]
            if a.hout is None and b.hin is None:
                p.lineTo(b.p)
            else:
                p.cubicTo(a.hout if a.hout is not None else a.p, b.hin if b.hin is not None else b.p, b.p)
        if sb.closed:
            p.closeSubpath()
    if rule is not None:
        p.setFillRule(rule)
    return p


def make_smooth(n, prev=None, nxt=None):
    """give a node symmetrical handles along the direction prev->next"""
    a = prev.p if prev is not None else n.p
    b = nxt.p if nxt is not None else n.p
    d = b - a
    L = math.hypot(d.x(), d.y())
    if L < 1e-6:
        return
    u = QPointF(d.x() / L, d.y() / L)
    l1 = math.hypot(*( (n.p - a).x(), (n.p - a).y())) / 3 if prev is not None else L / 3
    l2 = math.hypot((b - n.p).x(), (b - n.p).y()) / 3 if nxt is not None else L / 3
    n.hin = n.p - u * (l1 if l1 > 0 else L / 3) if prev is not None else None
    n.hout = n.p + u * (l2 if l2 > 0 else L / 3) if nxt is not None else None
    n.smooth = True


def smooth_polyline(pts, closed=False, tension=1 / 6):
    """freehand points -> nodes with catmull-rom style handles"""
    ns = [Node(p) for p in pts]
    m = len(ns)
    for i, n in enumerate(ns):
        if not closed and (i == 0 or i == m - 1):
            if i == 0 and m > 1:
                n.hout = n.p + (ns[1].p - n.p) * (1 / 3)
            if i == m - 1 and m > 1:
                n.hin = n.p + (ns[i - 1].p - n.p) * (1 / 3)
            continue
        a, b = ns[(i - 1) % m].p, ns[(i + 1) % m].p
        d = (b - a) * tension
        n.hin, n.hout, n.smooth = n.p - d, n.p + d, True
    return ns


def simplify(points, tol):
    """Ramer-Douglas-Peucker"""
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    dx, dy = b.x() - a.x(), b.y() - a.y()
    L = math.hypot(dx, dy)
    best, idx = -1, 0
    for i in range(1, len(points) - 1):
        p = points[i]
        d = math.hypot(p.x() - a.x(), p.y() - a.y()) if L < 1e-9 else abs(dy * (p.x() - a.x()) - dx * (p.y() - a.y())) / L
        if d > best:
            best, idx = d, i
    if best > tol:
        return simplify(points[:idx + 1], tol)[:-1] + simplify(points[idx:], tol)
    return [a, b]
