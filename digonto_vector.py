"""Digonto Vector - draw logos, shapes and illustrations. Easy like CorelDRAW, free on Ubuntu, macOS and Windows. PySide6 only."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSettings, QSize, QSizeF, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QImage, QPageSize, QPainter, QPdfWriter, QTransform
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QTabBar,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QToolBar, QToolButton, QVBoxLayout, QWidget)

import digonto_ui as ui
from vkit import __version__ as VERSION
from vkit import ops
from vkit.model import PX_PER_MM, VGroup, VItem, geom_rect, layers_from, load_into, leaves_of, top_level, union_rect
from vkit.svgio import export_svg, import_svg
from vkit.tools import (ArtboardTool, EyedropperTool, HandTool, NodeTool, PencilTool, PenTool, SelectTool, ShapeTool, TextTool, ZoomTool)
from vkit.panels import ColourPanel, HistoryPanel, LayersPanel, Navigator
from vkit.view import VectorView
from vkit.widgets import Palette, Ruler, tool_icon

APP_NAME = "Digonto Vector"
SLUG = "digonto-vector"
PAGES = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "A6": (105, 148), "Letter": (215.9, 279.4), "Legal": (215.9, 355.6),
         "Square 200 mm": (200, 200), "Business card": (90, 55), "Sticker 100 mm": (100, 100)}
HINTS = {
    "select": "Click to select, drag to move. Handles resize; drag just outside a corner (or the round handle) to rotate. Shift adds. Click once more on one of several to make it the key object for Align. Double-click edits nodes or text.",
    "node": "Drag nodes and handles. Double-click a line to add a node, double-click a node to smooth it. Delete removes nodes.",
    "pen": "Click for corners, click-drag for curves. Click the first point to close. Enter finishes, Esc cancels.",
    "rect": "Drag to draw. Shift = square, Ctrl = from the centre. Set corner roundness above.",
    "ellipse": "Drag to draw. Shift = circle, Ctrl = from the centre.",
    "polygon": "Drag from the centre. Set the number of sides above.",
    "star": "Drag from the centre. Set points and sharpness above.",
    "line": "Drag to draw a line. Shift snaps the angle.",
    "pencil": "Draw freehand; the line is smoothed. End near the start to close the shape.",
    "text": "Click on the page and type. Click a text again to edit it.",
    "eyedropper": "Click an object to copy its colours onto the selected objects.",
    "zoom": "Click to zoom in, right-click to zoom out, drag a box to zoom to it.",
    "hand": "Drag to move the view. (Hold Space with any tool.)",
}


def base_dir():
    env = (os.environ.get("DIGONTO_BASE") or os.environ.get("PROTTOY_BASE"))
    return env if env else os.path.dirname(os.path.dirname(HERE))


def add_recent(path):
    rp = os.path.join(base_dir(), "recent.json")
    try:
        with open(rp, encoding="utf-8") as f:
            items = [r for r in json.load(f) if isinstance(r, dict)]
    except (OSError, ValueError):
        items = []
    items = [r for r in items if r.get("path") != path]
    items.insert(0, {"app": SLUG, "path": path, "time": time.time()})
    try:
        os.makedirs(base_dir(), exist_ok=True)
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(items[:40], f)
    except OSError:
        pass


class ColorButton(QPushButton):
    def __init__(self, color="#C2395C"):
        super().__init__()
        self.color = color
        self.setFixedSize(46, 24)
        self.paint_color()

    def set_color(self, c):
        self.color = c
        self.paint_color()

    def paint_color(self):
        self.setStyleSheet(f"QPushButton{{background:{self.color if self.color not in (None, 'none') else '#FFFFFF'};border:1px solid #736A7A;border-radius:4px;}}")


class PageDialog(QDialog):
    def __init__(self, parent, title, w=210.0, h=297.0):
        super().__init__(parent)
        self.setWindowTitle(title)
        f = QFormLayout(self)
        self.preset = QComboBox()
        self.preset.addItem("Custom")
        self.preset.addItems(PAGES.keys())
        self.w = QDoubleSpinBox(); self.w.setRange(1, 5000); self.w.setSuffix(" mm"); self.w.setValue(w)
        self.h = QDoubleSpinBox(); self.h.setRange(1, 5000); self.h.setSuffix(" mm"); self.h.setValue(h)
        self.land = QCheckBox("Landscape")
        f.addRow("Size", self.preset); f.addRow("Width", self.w); f.addRow("Height", self.h); f.addRow("", self.land)
        self.preset.currentTextChanged.connect(self.pick)
        self.land.toggled.connect(lambda _: (lambda a, b: (self.w.setValue(b), self.h.setValue(a)))(self.w.value(), self.h.value()))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)

    def pick(self, name):
        if name in PAGES:
            w, h = PAGES[name]
            if self.land.isChecked():
                w, h = h, w
            self.w.setValue(w); self.h.setValue(h)


class Main(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.icon = ui.app_icon(HERE, SLUG)
        self.setWindowIcon(self.icon)
        self._busy = False
        v = self.v = VectorView()
        v.text_opt = {"family": "Sans Serif", "size": 48.0, "bold": False, "italic": False}
        for name, tool in (("select", SelectTool(v)), ("node", NodeTool(v)), ("pen", PenTool(v)), ("rect", ShapeTool(v, "rect")),
                           ("ellipse", ShapeTool(v, "ellipse")), ("polygon", ShapeTool(v, "polygon")), ("star", ShapeTool(v, "star")),
                           ("line", ShapeTool(v, "line")), ("pencil", PencilTool(v)), ("text", TextTool(v)),
                           ("eyedropper", EyedropperTool(v)), ("zoom", ZoomTool(v)), ("hand", HandTool(v)), ("artboard", ArtboardTool(v))):
            v.register(name, tool)
        self._build_ui()
        self._build_menus()
        v.selectionChanged2.connect(self.on_selection)
        v.docChanged.connect(self.on_doc)
        v.message.connect(lambda m: self.statusBar().showMessage(m, 4000))
        v.cursorMoved.connect(self.on_cursor)
        v.zoomChanged.connect(self.on_zoom)
        v.toolRequest.connect(self.on_tool_request)
        v.setContextMenuPolicy(Qt.CustomContextMenu)
        v.customContextMenuRequested.connect(self.context_menu)
        self.tool_actions["select"].setChecked(True)
        self.use_tool("select")
        ui.fit_window(self, 1320, 880)
        QTimer.singleShot(0, v.fit_page)
        self.on_selection()
        self.on_doc()
        self.restore_layout()
        self._add_tab()
        self.show_start()
        if path:
            self.open_path(path)

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        v = self.v
        hd = ui.make_header(APP_NAME, "Draw logos, shapes and illustrations", self.icon, self.about)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.propbar = self._build_optbar()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        corner = QFrame(); corner.setFixedSize(20, 20); corner.setStyleSheet("background:#ECEFEE;")
        self.ruler_h, self.ruler_v = Ruler(v, True), Ruler(v, False)
        self.rulers = [corner, self.ruler_h, self.ruler_v]
        grid.addWidget(corner, 0, 0); grid.addWidget(self.ruler_h, 0, 1); grid.addWidget(self.ruler_v, 1, 0); grid.addWidget(v, 1, 1)
        self.dtabs = QTabBar()
        self.dtabs.setTabsClosable(True)
        self.dtabs.setExpanding(False)
        self.dtabs.setDocumentMode(True)
        self.dtabs.currentChanged.connect(self._tab_changed)
        self.dtabs.tabCloseRequested.connect(self.close_tab)
        self.docs, self._cur, self._untitled, self._switching = [], -1, 0, False
        bl.addWidget(self.dtabs)
        bl.addLayout(grid, 1)
        self.palette = Palette()
        self.palette.pickFill.connect(lambda c: ops.set_style(v, fill={"type": "none"} if c is None else {"type": "solid", "c1": c}, label="Fill colour") or self.sync_panel())
        self.palette.pickStroke.connect(lambda c: self.set_outline_color(c))
        bl.addWidget(self.palette)
        # a window inside the window: the side panels dock here, so they start below the header (like Digonto Images)
        self.inner = QMainWindow()
        self.inner.setWindowFlags(Qt.Widget)
        self.start = ui.StartScreen(
            "Draw logos, shapes and illustrations - SVG, PDF, EPS and AI",
            [("New drawing", "new", self.start_new, True), ("Open …", "open", self.open_dialog, False)],
            "or drop a drawing here (SVG, PDF, EPS, AI)", lambda: ui.recent_paths(SLUG), self.open_path)
        from PySide6.QtWidgets import QStackedWidget
        self.centre = QStackedWidget()
        self.centre.addWidget(self.start)
        self.centre.addWidget(body)
        self.inner.setCentralWidget(self.centre)
        self.setAcceptDrops(True)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(hd)
        cl.addWidget(self.propbar)          # the option bar spans the whole window; the side panels start below it
        cl.addWidget(self.inner, 1)
        self.setCentralWidget(central)

        # tools: a plain icon column on the left
        self.tools_bar = QToolBar("Tools")
        self.tools_bar.setOrientation(Qt.Vertical)
        self.tools_bar.setMovable(False)
        self.tools_bar.setIconSize(QSize(28, 28))
        self.tool_actions = {}
        # the Digonto file icons at the top of the tool column (same order as every Digonto program)
        self.file_actions = {}
        for key, kind, tip, fn in (("new", "new", "New drawing (Ctrl+N)", self.start_new), ("open", "open", "Open (Ctrl+O)", self.open_dialog),
                                   ("save", "save", "Save (Ctrl+S)", self.save), ("export", "export", "Save as / export", self.rail_export),
                                   ("print", "print", "Print (Ctrl+P)", self.print_doc)):
            fa = QAction(ui.line_icon(kind, 24), tip.split(" (")[0], self)
            fa.setToolTip(tip)
            fa.triggered.connect(lambda _=False, f=fn: f())
            self.tools_bar.addAction(fa)
            self.file_actions[key] = fa
        self.tools_bar.addSeparator()
        grp = QActionGroup(self)
        # the same keys as Illustrator, so nobody has to learn them again
        for name, label, key in (("select", "Select", "V"), ("node", "Node edit (direct select)", "A"), ("pen", "Pen (curves)", "P"),
                                 ("rect", "Rectangle", "M"), ("ellipse", "Ellipse", "L"), ("polygon", "Polygon", "Y"), ("star", "Star", "S"),
                                 ("line", "Line", "\\"), ("pencil", "Pencil (freehand)", "N"), ("text", "Text", "T"), ("eyedropper", "Eyedropper", "I"),
                                 ("artboard", "Artboard", "Shift+O"), ("zoom", "Zoom", "Z"), ("hand", "Hand (or hold Space)", "H")):
            a = QAction(tool_icon(name), label, self)
            a.setCheckable(True)
            a.setShortcut(key)
            a.setToolTip(f"{label}  ({key})")
            a.triggered.connect(lambda _=False, n=name: self.use_tool(n))
            grp.addAction(a)
            self.tools_bar.addAction(a)
            self.tool_actions[name] = a
            if name in ("pen", "line", "eyedropper"):
                self.tools_bar.addSeparator()
        self.d_tools = QDockWidget("Tools", self.inner)
        self.d_tools.setObjectName("Tools")
        self.d_tools.setWidget(self.tools_bar)
        self.d_tools.setTitleBarWidget(QWidget())
        self.d_tools.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.inner.addDockWidget(Qt.LeftDockWidgetArea, self.d_tools)

        # one column on the right that scrolls: Navigator, Properties, Colour, History, Layers ... (same idea as Images)
        self.navigator = Navigator(v)
        self.colour = ColourPanel()
        self.colour.colourPicked.connect(self.colour_picked)
        self.history = HistoryPanel(v)
        self.layers_panel = LayersPanel(v)
        self.stack = ui.PanelStack("Digonto", "Vector")
        self.sections = {}

        def section(title, w):
            self.sections[title] = self.stack.add(title, w)
        section("Navigator", self.navigator)
        section("Properties", self._panel_props())
        section("Colour", self.colour)
        section("History", self.history)
        section("Layers", self.layers_panel)
        section("Artboards", self._panel_artboards())
        section("Align", self._panel_align())
        section("Pathfinder", self._panel_shaping())
        section("Effects", self._panel_effects())
        self.stack.restore()
        ui.shrinkable(self.stack)
        self.d_side = QDockWidget("Panels", self.inner)
        self.d_side.setObjectName("Panels")
        self.d_side.setTitleBarWidget(QWidget())
        self.d_side.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.d_side.setWidget(ui.ScrollColumn(self.stack))
        self.inner.addDockWidget(Qt.RightDockWidgetArea, self.d_side)
        self.inner.resizeDocks([self.d_side], [ui.dock_width_for(self.width(), self.stack.minimumSizeHint().width())], Qt.Horizontal)

        sb = self.statusBar()
        self.lbl_pos = QLabel("X 0.0  Y 0.0 mm")
        self.lbl_sel = QLabel("")
        self.lbl_zoom = QLabel("100 %")
        for w in (self.lbl_pos, self.lbl_sel, self.lbl_zoom):
            w.setMinimumWidth(130)
            sb.addPermanentWidget(w)
        self.lbl_hint = QLabel("")
        sb.addWidget(self.lbl_hint, 1)

    def _spin(self, lo, hi, val, step=1.0, dec=2, suffix=""):
        s = QDoubleSpinBox()
        s.setRange(lo, hi); s.setDecimals(dec); s.setSingleStep(step); s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        return s

    def _build_optbar(self):
        bar = QWidget()
        bar.setStyleSheet("background:#F4F6F5; border-bottom:1px solid #D5DBD8;")
        h = ui.FlowLayout(bar, 8, 12, 3)
        self.opt_hint = QLabel("")
        # tool options (only the ones for the active tool are shown)
        self.opt_widgets = {}
        self.o_radius = self._spin(0, 500, 0, 1, 1, " mm"); self.o_radius.valueChanged.connect(lambda x: self.v.opt.__setitem__("radius", x))
        g = QWidget(); gl = QHBoxLayout(g); gl.setContentsMargins(0, 0, 0, 0); gl.addWidget(QLabel("Corner roundness")); gl.addWidget(self.o_radius)
        self.opt_widgets["rect"] = g
        self.o_sides = QSpinBox(); self.o_sides.setRange(3, 60); self.o_sides.setValue(6); self.o_sides.valueChanged.connect(lambda x: self.v.opt.__setitem__("sides", x))
        g = QWidget(); gl = QHBoxLayout(g); gl.setContentsMargins(0, 0, 0, 0); gl.addWidget(QLabel("Sides")); gl.addWidget(self.o_sides)
        self.opt_widgets["polygon"] = g
        self.o_points = QSpinBox(); self.o_points.setRange(3, 60); self.o_points.setValue(5); self.o_points.valueChanged.connect(lambda x: self.v.opt.__setitem__("points", x))
        self.o_inner = QSpinBox(); self.o_inner.setRange(5, 95); self.o_inner.setValue(50); self.o_inner.setSuffix(" %"); self.o_inner.valueChanged.connect(lambda x: self.v.opt.__setitem__("inner", x / 100.0))
        g = QWidget(); gl = QHBoxLayout(g); gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(QLabel("Points")); gl.addWidget(self.o_points); gl.addWidget(QLabel("Sharpness")); gl.addWidget(self.o_inner)
        self.opt_widgets["star"] = g
        self.o_font = QFontComboBox(); self.o_font.setMaximumWidth(190)
        self.o_size = self._spin(2, 1000, 36, 1, 0, " pt")
        self.o_bold = QCheckBox("Bold"); self.o_italic = QCheckBox("Italic")
        g = QWidget(); gl = QHBoxLayout(g); gl.setContentsMargins(0, 0, 0, 0)
        for w in (self.o_font, self.o_size, self.o_bold, self.o_italic):
            gl.addWidget(w)
        self.opt_widgets["text"] = g
        self.o_size.valueChanged.connect(self.text_opts_changed)
        self.o_bold.toggled.connect(self.text_opts_changed)
        self.o_italic.toggled.connect(self.text_opts_changed)
        self.o_font.currentFontChanged.connect(self.text_opts_changed)
        # node buttons
        g = QWidget(); gl = QHBoxLayout(g); gl.setContentsMargins(0, 0, 0, 0)
        for text, tip, op in (("+ Node", "Add node (double-click a line)", "add_nodes"), ("− Node", "Delete selected nodes (Delete)", "delete_nodes"),
                              ("To line", "Make the selected segments straight", "set_lines"), ("To curve", "Make the selected segments curved", "set_curves"),
                              ("Smooth", "Smooth node", "make_smooth"), ("Corner", "Sharp corner node", "make_cusp"),
                              ("Close", "Close the path", "close_path"), ("Break", "Break the path at the node", "break_at")):
            b = QPushButton(text); b.setToolTip(tip); b.clicked.connect(lambda _=False, o=op: self.node_op(o)); gl.addWidget(b)
        self.opt_widgets["node"] = g
        for w in self.opt_widgets.values():
            h.addWidget(w)
        self.opt_none = QLabel("Options for this tool appear here.")
        self.opt_none.setStyleSheet("color:#736A7A;")
        h.addWidget(self.opt_none)
        self._cur_opt = None

        def apply_opt(name):
            self._cur_opt = name
            for k, w in self.opt_widgets.items():
                w.setVisible(k == name)
            self.opt_none.setVisible(name not in self.opt_widgets)
        ui.stabilize_bar(bar, h, list(self.opt_widgets) + [None], apply_opt, lambda: self._cur_opt)
        return bar

    def _panel_fill(self):
        w = QWidget()
        f = QFormLayout(w)
        self.f_type = QComboBox(); self.f_type.addItems(["None", "Solid", "Linear gradient", "Radial gradient"])
        self.f_c1, self.f_c2 = ColorButton(), ColorButton("#FFFFFF")
        self.f_angle = QSpinBox(); self.f_angle.setRange(-360, 360); self.f_angle.setSuffix("°")
        self.s_color = ColorButton("#1B1040")
        self.s_none = QCheckBox("No outline")
        self.s_width = self._spin(0, 500, 0.5, 0.1, 2, " mm")
        self.s_dash = QComboBox(); self.s_dash.addItems(["solid", "dash", "dot", "dashdot"])
        self.s_join = QComboBox(); self.s_join.addItems(["miter", "round", "bevel"])
        self.s_cap = QComboBox(); self.s_cap.addItems(["flat", "round", "square"])
        self.s_op = QSlider(Qt.Horizontal); self.s_op.setRange(0, 100); self.s_op.setValue(100)
        f.addRow(QLabel("<b>Fill</b>"))
        f.addRow("Type", self.f_type); f.addRow("Colour", self.f_c1); f.addRow("Colour 2", self.f_c2); f.addRow("Angle", self.f_angle)
        f.addRow(QLabel("<b>Outline</b>"))
        f.addRow("Colour", self.s_color); f.addRow("", self.s_none); f.addRow("Width", self.s_width); f.addRow("Style", self.s_dash)
        f.addRow("Corners", self.s_join); f.addRow("Line ends", self.s_cap)
        f.addRow(QLabel("<b>Object</b>"))
        f.addRow("Opacity", self.s_op)
        self.f_type.currentIndexChanged.connect(self.fill_changed)
        self.f_angle.valueChanged.connect(self.fill_changed)
        self.f_c1.clicked.connect(lambda: self.pick_color(self.f_c1, self.fill_changed))
        self.f_c2.clicked.connect(lambda: self.pick_color(self.f_c2, self.fill_changed))
        self.s_color.clicked.connect(lambda: self.pick_color(self.s_color, self.stroke_changed))
        self.s_none.toggled.connect(self.stroke_changed)
        self.s_width.valueChanged.connect(self.stroke_changed)
        for c in (self.s_dash, self.s_join, self.s_cap):
            c.currentIndexChanged.connect(self.stroke_changed)
        self.s_op.valueChanged.connect(self.opacity_changed)
        return w

    def _panel_align(self):
        w = QWidget()
        l = QVBoxLayout(w)
        row = QHBoxLayout(); row.addWidget(QLabel("Align to"))
        self.al_to = QComboBox()
        for t, d in (("Selection", "selection"), ("Key object", "key"), ("Page", "page")):
            self.al_to.addItem(t, d)
        self.al_to.setToolTip("Key object: click once more on one of the selected objects; it stays, the others move to it")
        row.addWidget(self.al_to, 1); l.addLayout(row)
        g = QGridLayout()
        items = [("Left", "left"), ("Centre", "hcenter"), ("Right", "right"), ("Top", "top"), ("Middle", "vcenter"), ("Bottom", "bottom")]
        for k, (t, m) in enumerate(items):
            b = QPushButton(t); b.clicked.connect(lambda _=False, mm=m: self.do_align(mm))
            g.addWidget(b, k // 3, k % 3)
        l.addLayout(g)
        b = QPushButton("Centre on page"); b.clicked.connect(lambda: ops.center_on_page(self.v)); l.addWidget(b)
        l.addWidget(QLabel("<b>Distribute (3 or more)</b>"))
        g = QGridLayout()
        for k, (t, ax, md) in enumerate((("Equal gaps →", "h", "gaps"), ("Centres →", "h", "centers"), ("Equal gaps ↓", "v", "gaps"), ("Centres ↓", "v", "centers"))):
            b = QPushButton(t); b.clicked.connect(lambda _=False, a=ax, m=md: ops.distribute(self.v, a, m)); g.addWidget(b, k // 2, k % 2)
        l.addLayout(g)
        l.addWidget(QLabel("<b>Step and repeat</b>"))
        fr = QFormLayout()
        self.sr_n = QSpinBox(); self.sr_n.setRange(1, 500); self.sr_n.setValue(5)
        self.sr_dx = self._spin(-9999, 9999, 20, 1, 1, " mm"); self.sr_dy = self._spin(-9999, 9999, 0, 1, 1, " mm")
        fr.addRow("Copies", self.sr_n); fr.addRow("Across", self.sr_dx); fr.addRow("Down", self.sr_dy)
        l.addLayout(fr)
        b = QPushButton("Make copies"); b.clicked.connect(lambda: ops.step_repeat(self.v, self.sr_n.value(), self.sr_dx.value(), self.sr_dy.value())); l.addWidget(b)
        l.addStretch()
        return w

    def transform_dialog(self, kind):
        """Object > Transform > Move / Rotate / Reflect / Scale, as in Illustrator: OK transforms, Copy transforms a copy"""
        if not self.v.selected():
            self.v.message.emit("Select something first")
            return
        d = QDialog(self); d.setWindowTitle({"move": "Move", "rotate": "Rotate", "reflect": "Reflect", "scale": "Scale"}[kind])
        f = QFormLayout(d)
        w = {}
        def spin(lo, hi, val, suf, dec=2):
            sb = QDoubleSpinBox(); sb.setRange(lo, hi); sb.setDecimals(dec); sb.setValue(val); sb.setSuffix(suf); return sb
        if kind == "move":
            w["h"] = spin(-100000, 100000, 0, " mm"); w["v"] = spin(-100000, 100000, 0, " mm")
            f.addRow("Horizontal", w["h"]); f.addRow("Vertical", w["v"])
        elif kind == "rotate":
            w["a"] = spin(-360, 360, 45, "°"); f.addRow("Angle", w["a"])
        elif kind == "reflect":
            w["axis"] = QComboBox(); w["axis"].addItems(["Vertical axis (left ↔ right)", "Horizontal axis (top ↕ bottom)", "Angle"])
            w["a"] = spin(-360, 360, 45, "°"); f.addRow("Axis", w["axis"]); f.addRow("Angle", w["a"])
        else:
            w["uni"] = QCheckBox("Uniform"); w["uni"].setChecked(True)
            w["s"] = spin(0.01, 100000, 100, " %"); w["sh"] = spin(0.01, 100000, 100, " %"); w["sv"] = spin(0.01, 100000, 100, " %")
            f.addRow("", w["uni"]); f.addRow("Scale", w["s"]); f.addRow("Horizontal", w["sh"]); f.addRow("Vertical", w["sv"])
            def upd():
                for k in ("sh", "sv"):
                    w[k].setEnabled(not w["uni"].isChecked())
                w["s"].setEnabled(w["uni"].isChecked())
            w["uni"].toggled.connect(upd); upd()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        cp = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        f.addRow(bb)
        res = {"copy": False}
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        cp.clicked.connect(lambda: (res.__setitem__("copy", True), d.accept()))
        if d.exec() != QDialog.Accepted:
            return
        from vkit.model import PX_PER_MM
        if kind == "move":
            op = ("move", w["h"].value() * PX_PER_MM, w["v"].value() * PX_PER_MM)
        elif kind == "rotate":
            op = ("rotate", -w["a"].value())              # positive = anticlockwise, as in Illustrator
        elif kind == "reflect":
            op = ("reflect", [90.0, 0.0, -w["a"].value()][w["axis"].currentIndex()])
        else:
            if w["uni"].isChecked():
                op = ("scale", w["s"].value() / 100, w["s"].value() / 100)
            else:
                op = ("scale", w["sh"].value() / 100, w["sv"].value() / 100)
        ops.transform_op(self.v, op + (("copy",) if res["copy"] else ()))

    def do_align(self, mode):
        to = self.al_to.currentData()
        if to == "key" and self.v.key_object is None and len(self.v.selected()) > 1:
            self.v.message.emit("Click once more on the object the others should align to")
            return
        if to == "selection" and self.v.key_object is not None:
            to = "key"                               # as in Illustrator: a key object always wins
        ops.align(self.v, mode, to=to)

    def _panel_shaping(self):
        w = QWidget()
        l = QVBoxLayout(w)
        _sl = QLabel("<b>Pathfinder</b> (select 2 or more shapes)"); _sl.setWordWrap(True); l.addWidget(_sl)
        grid = QGridLayout(); grid.setSpacing(4)
        for i, (t, tip, m) in enumerate((("Unite", "Join into one shape", "weld"), ("Minus front", "The shapes in front cut away from the back shape", "front_minus_back"),
                          ("Intersect", "Keep only the overlapping part", "intersect"), ("Exclude", "Remove the overlapping parts", "exclude"),
                          ("Divide", "Cut into all separate pieces", "divide"), ("Trim", "Shapes in front cut the shapes behind; all stay", "trim"),
                          ("Minus back", "The shapes behind cut away from the front shape", "back_minus_front"))):
            b = QPushButton(t); b.setToolTip(tip); b.clicked.connect(lambda _=False, mm=m: ops.shaping(self.v, mm)); grid.addWidget(b, i // 2, i % 2)
        l.addLayout(grid)
        l.addWidget(QLabel("<b>Curves</b>"))
        for t, fn in (("Combine (make holes)", lambda: ops.combine(self.v)), ("Break apart", lambda: ops.break_apart(self.v)),
                      ("Outline → object", lambda: ops.outline_to_object(self.v)), ("Text → curves", lambda: ops.to_curves(self.v))):
            b = QPushButton(t); b.clicked.connect(fn); l.addWidget(b)
        l.addWidget(QLabel("<b>Contour</b> (offset copies)"))
        fr = QFormLayout()
        self.c_d = self._spin(0.1, 500, 3, 0.5, 1, " mm"); self.c_n = QSpinBox(); self.c_n.setRange(1, 50); self.c_n.setValue(3)
        self.c_out = QCheckBox("Outside"); self.c_out.setChecked(True)
        fr.addRow("Distance", self.c_d); fr.addRow("Steps", self.c_n); fr.addRow("", self.c_out)
        l.addLayout(fr)
        b = QPushButton("Make contour"); b.clicked.connect(lambda: ops.contour(self.v, self.c_d.value(), self.c_n.value(), self.c_out.isChecked())); l.addWidget(b)
        l.addStretch()
        return w

    def _panel_props(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        g = QGridLayout()
        self.px, self.py = self._spin(-99999, 99999, 0, 1, 1, ""), self._spin(-99999, 99999, 0, 1, 1, "")
        self.pw, self.ph = self._spin(0.01, 99999, 0, 1, 1, ""), self._spin(0.01, 99999, 0, 1, 1, "")
        self.lock = QCheckBox("Keep proportions")
        self.lock.setChecked(True)
        g.setHorizontalSpacing(4)
        for (lab, wd, r, c) in (("X", self.px, 0, 0), ("W", self.pw, 0, 2), ("Y", self.py, 1, 0), ("H", self.ph, 1, 2)):
            g.addWidget(QLabel(lab), r, c)
            wd.setMinimumWidth(52); wd.setMaximumWidth(86)
            g.addWidget(wd, r, c + 1)
        g.setColumnStretch(1, 1); g.setColumnStretch(3, 1)
        l.addLayout(g)
        l.addWidget(self.lock)
        self.px.editingFinished.connect(lambda: self.geo_edit("x"))
        self.py.editingFinished.connect(lambda: self.geo_edit("y"))
        self.pw.editingFinished.connect(lambda: self.geo_edit("w"))
        self.ph.editingFinished.connect(lambda: self.geo_edit("h"))
        row = QHBoxLayout()
        for text, tip, fn in (("⟲", "Rotate 90° left", lambda: ops.rotate(self.v, -90)), ("⟳", "Rotate 90° right", lambda: ops.rotate(self.v, 90)),
                              ("⇋", "Mirror left-right", lambda: ops.mirror(self.v, True)), ("⇅", "Mirror top-bottom", lambda: ops.mirror(self.v, False))):
            b = QToolButton(); b.setText(text); b.setToolTip(tip); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch()
        l.addLayout(row)
        row = QHBoxLayout()
        self.rot_a = self._spin(-360, 360, 15, 1, 1, "°")
        b = QPushButton("Rotate"); b.clicked.connect(lambda: ops.rotate(self.v, self.rot_a.value()))
        self.rot_a.setMinimumWidth(60)
        row.addWidget(self.rot_a, 1); row.addWidget(b)
        l.addLayout(row)
        row = QHBoxLayout()
        self.sk_x, self.sk_y = self._spin(-80, 80, 15, 1, 1, "°"), self._spin(-80, 80, 0, 1, 1, "°")
        b = QPushButton("Skew"); b.setToolTip("Slant across / down"); b.clicked.connect(lambda: ops.skew(self.v, self.sk_x.value(), self.sk_y.value()))
        self.sk_x.setMinimumWidth(50); self.sk_y.setMinimumWidth(50)
        row.addWidget(self.sk_x); row.addWidget(self.sk_y); row.addWidget(b)
        l.addLayout(row)
        l.addWidget(self._panel_fill())
        return w

    def _panel_effects(self):
        w = QWidget()
        l = QVBoxLayout(w)
        b = QPushButton("Clip to shape"); b.setToolTip("Select objects and, in front of them, a shape: the objects are cut to that shape")
        b.clicked.connect(lambda: ops.clip_to_top(self.v)); l.addWidget(b)
        fr = QFormLayout()
        self.bl_n = QSpinBox(); self.bl_n.setRange(1, 200); self.bl_n.setValue(5)
        fr.addRow("Blend steps", self.bl_n)
        l.addLayout(fr)
        b = QPushButton("Blend two objects"); b.setToolTip("Makes copies between the lowest and the highest selected object")
        b.clicked.connect(lambda: ops.blend(self.v, self.bl_n.value())); l.addWidget(b)
        fr = QFormLayout()
        self.rc_r = self._spin(0.1, 200, 4, 0.5, 1, " mm")
        fr.addRow("Corner radius", self.rc_r)
        l.addLayout(fr)
        b = QPushButton("Round corners"); b.clicked.connect(lambda: ops.round_corners(self.v, self.rc_r.value())); l.addWidget(b)
        b = QPushButton("Trace image…"); b.setToolTip("Turn a picture (logo, scan, drawing) into vector shapes")
        b.clicked.connect(self.trace_dialog); l.addWidget(b)
        l.addStretch()
        return w

    # ------------------------------------------------------------------ menus
    def act(self, menu, text, fn, key=None, checkable=False, checked=False):
        a = QAction(text, self)
        if key:
            a.setShortcut(key)
        if checkable:
            a.setCheckable(True); a.setChecked(checked)
            a.toggled.connect(fn)
        else:
            a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        return a

    def _build_menus(self):
        v = self.v
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        self.act(fm, "New…", self.new_doc, "Ctrl+N")
        self.act(fm, "Open…", self.open_dialog, "Ctrl+O")
        fm.addSeparator()
        self.act(fm, "Save", self.save, "Ctrl+S")
        self.act(fm, "Save as…", self.save_as, "Ctrl+Shift+S")
        self.act(fm, "Print…", self.print_doc, "Ctrl+P")
        self.act(fm, "Close project", lambda: self.close_tab(self.dtabs.currentIndex()), "Ctrl+W")
        fm.addSeparator()
        self.act(fm, "Import (SVG, AI, EPS, PDF)…", self.import_svg_dialog, "Ctrl+I")
        self.act(fm, "Trace image…", self.trace_dialog)
        em = fm.addMenu("Export")
        self.act(em, "SVG…", lambda: self.export("svg"), "Ctrl+E")
        self.act(em, "SVG (selection only)…", lambda: self.export("svg", True))
        self.act(em, "PNG image…", lambda: self.export("png"))
        self.act(em, "PDF…", lambda: self.export("pdf"))
        self.act(em, "EPS (Illustrator, Inkscape)…", lambda: self.export("eps"))
        self.act(em, "AI (Adobe Illustrator)…", lambda: self.export("ai"))
        self.act(em, "All artboards…", self.export_artboards, "Ctrl+Alt+E")
        fm.addSeparator()
        self.act(fm, "Page size (active artboard)…", self.page_setup)
        self.act(fm, "New artboard", self.add_artboard)
        fm.addSeparator()
        fm.addAction(QAction("Exit", self, shortcut="Ctrl+Q", triggered=self.close))
        fm.addAction(QAction("Quit (force)", self, shortcut="Ctrl+Shift+Q", triggered=lambda: os._exit(0)))

        em = mb.addMenu("&Edit")
        self.act(em, "Undo", v.undo, "Ctrl+Z")
        self.act(em, "Redo", v.redo, "Ctrl+Shift+Z")
        em.addSeparator()
        self.act(em, "Cut", lambda: ops.cut(v), "Ctrl+X")
        self.act(em, "Copy", lambda: ops.copy_sel(v), "Ctrl+C")
        self.act(em, "Paste", lambda: ops.paste(v), "Ctrl+V")
        self.act(em, "Paste in place", lambda: ops.paste(v, True), "Ctrl+Shift+V")
        self.act(em, "Duplicate", lambda: ops.duplicate(v), "Ctrl+Alt+D")
        self.act(em, "Delete", lambda: ops.delete(v), "Del")
        em.addSeparator()
        self.act(em, "Select all", lambda: ops.select_all(v), "Ctrl+A")
        self.act(em, "Deselect", v.scene_.clearSelection, "Ctrl+Shift+A")
        self.act(em, "Paste in front", lambda: ops.paste(v, True), "Ctrl+F")
        self.act(em, "Paste in back", lambda: (ops.paste(v, True), ops.arrange(v, "back")), "Ctrl+B")
        self.act(em, "Invert selection", lambda: ops.invert_selection(v))

        om = mb.addMenu("&Object")
        am = om.addMenu("Arrange")
        self.act(am, "To front", lambda: ops.arrange(v, "front"), "Ctrl+Shift+]")
        self.act(am, "Forward one", lambda: ops.arrange(v, "forward"), "Ctrl+]")
        self.act(am, "Back one", lambda: ops.arrange(v, "backward"), "Ctrl+[")
        self.act(am, "To back", lambda: ops.arrange(v, "back"), "Ctrl+Shift+[")
        self.act(om, "Group", lambda: ops.group(v), "Ctrl+G")
        self.act(om, "Ungroup", lambda: ops.ungroup(v), "Ctrl+Shift+G")
        self.act(om, "Combine (compound path)", lambda: ops.combine(v), "Ctrl+8")
        self.act(om, "Break apart (release compound path)", lambda: ops.break_apart(v), "Ctrl+Alt+Shift+8")
        om.addSeparator()
        self.act(om, "Lock", lambda: ops.lock(v, True), "Ctrl+2")
        self.act(om, "Unlock all", lambda: ops.unlock_all(v), "Ctrl+Alt+2")
        self.act(om, "Hide", lambda: ops.hide(v), "Ctrl+3")
        self.act(om, "Show all", lambda: ops.show_all(v), "Ctrl+Alt+3")
        om.addSeparator()
        self.act(om, "Clip to shape (clipping mask)", lambda: ops.clip_to_top(v), "Ctrl+7")
        self.act(om, "Blend…", lambda: ops.blend(v, self.bl_n.value()))
        self.act(om, "Round corners", lambda: ops.round_corners(v, self.rc_r.value()))
        self.act(om, "Trace image…", self.trace_dialog)
        self.act(om, "Outline → object", lambda: ops.outline_to_object(v))
        self.act(om, "Text → curves (create outlines)", lambda: ops.to_curves(v), "Ctrl+Shift+O")
        tm = om.addMenu("Transform")
        self.act(tm, "Transform again", lambda: ops.transform_again(v), "Ctrl+D")
        tm.addSeparator()
        self.act(tm, "Move…", lambda: self.transform_dialog("move"), "Ctrl+Shift+M")
        self.act(tm, "Rotate…", lambda: self.transform_dialog("rotate"))
        self.act(tm, "Reflect…", lambda: self.transform_dialog("reflect"))
        self.act(tm, "Scale…", lambda: self.transform_dialog("scale"))
        tm.addSeparator()
        self.act(tm, "Mirror left-right", lambda: ops.mirror(v, True))
        self.act(tm, "Mirror top-bottom", lambda: ops.mirror(v, False))
        self.act(tm, "Rotate 90° right", lambda: ops.rotate(v, 90))
        self.act(tm, "Rotate 90° left", lambda: ops.rotate(v, -90))
        self.act(tm, "Rotate 180°", lambda: ops.rotate(v, 180))
        sm = mb.addMenu("&Pathfinder")
        for t, _tip, m in (("Unite", "Join into one shape", "weld"), ("Minus front", "The shapes in front cut away from the back shape", "front_minus_back"),
                          ("Intersect", "Keep only the overlapping part", "intersect"), ("Exclude", "Remove the overlapping parts", "exclude"),
                          ("Divide", "Cut into all separate pieces", "divide"), ("Trim", "Shapes in front cut the shapes behind; all stay", "trim"),
                          ("Minus back", "The shapes behind cut away from the front shape", "back_minus_front")):
            self.act(sm, t, lambda mm=m: ops.shaping(v, mm))

        nm = mb.addMenu("&Node")
        for t, o in (("Add node", "add_nodes"), ("Delete nodes", "delete_nodes"), ("Make line", "set_lines"), ("Make curve", "set_curves"),
                     ("Smooth node", "make_smooth"), ("Corner node", "make_cusp"), ("Close path", "close_path"), ("Break path", "break_at")):
            self.act(nm, t, lambda oo=o: self.node_op(oo))

        vm = mb.addMenu("&View")
        self.act(vm, "Zoom in", lambda: v.zoom_to(v.zoom_factor() * 1.5), "Ctrl+=")
        self.act(vm, "Zoom out", lambda: v.zoom_to(v.zoom_factor() / 1.5), "Ctrl+-")
        self.act(vm, "Fit page", v.fit_page, "Ctrl+0")
        self.act(vm, "Actual size (100 %)", lambda: v.zoom_to(1.0), "Ctrl+1")
        self.act(vm, "Zoom to selection", v.zoom_selection, "Ctrl+Alt+0")
        vm.addSeparator()
        self.a_grid = self.act(vm, "Show grid", lambda on: (v.grid.__setitem__("show", on), v.viewport().update()), "Ctrl+'", checkable=True)
        self.a_gsnap = self.act(vm, "Snap to grid", lambda on: v.grid.__setitem__("snap", on), checkable=True)
        self.act(vm, "Grid spacing…", self.grid_dialog)
        self.act(vm, "Snap to objects and page", lambda on: setattr(v, "snap_objects", on), checkable=True, checked=True)
        self.act(vm, "Snap to guides", lambda on: setattr(v, "snap_guides", on), checkable=True, checked=True)
        self.act(vm, "Show guides", lambda on: (setattr(v, "show_guides", on), v.viewport().update()), "Ctrl+;", checkable=True, checked=True)
        self.act(vm, "Clear all guides", self.clear_guides)
        vm.addSeparator()
        self.act(vm, "Wireframe view (outline)", self.set_wireframe, "Ctrl+Y", checkable=True)
        vm.addSeparator()
        ta = self.d_side.toggleViewAction()
        ta.setText("Side panels (all)")
        vm.addAction(ta)
        wm = vm.addMenu("Windows")                      # turn single panels on and off (remembered)
        qs = QSettings("Digonto", "Vector")
        for title, box in list(self.sections.items()) + [("Tools", self.d_tools)]:
            a = QAction(title, self, checkable=True)
            on = str(qs.value("panel_" + title, "true")).lower() != "false"
            a.setChecked(on)
            box.setVisible(on)

            def toggle(checked, b=box, t=title):
                b.setVisible(checked)
                QSettings("Digonto", "Vector").setValue("panel_" + t, "true" if checked else "false")
            a.toggled.connect(toggle)
            wm.addAction(a)
        wm.addSeparator()
        for label, key, fn in (("Colour palette (bottom)", "palette", lambda on: self.palette.setVisible(on)),
                               ("Rulers", "rulers", lambda on: [r.setVisible(on) for r in self.rulers]),
                               ("Options bar", "propbar", lambda on: self.propbar.setVisible(on))):
            a = QAction(label, self, checkable=True)
            on = str(qs.value("panel_" + key, "true")).lower() != "false"
            a.setChecked(on)
            fn(on)

            def tg(checked, f=fn, k=key):
                f(checked)
                QSettings("Digonto", "Vector").setValue("panel_" + k, "true" if checked else "false")
            a.toggled.connect(tg)
            wm.addAction(a)
        wm.addSeparator()
        wm.addAction(QAction("Show all panels", self, triggered=lambda: [x.setChecked(True) for x in wm.actions() if x.isCheckable()]))

        wm.addSeparator()
        for title, key in (("Align", "Shift+F7"), ("Layers", "F7"), ("Colour", "F6"), ("Pathfinder", "Ctrl+Shift+F9"), ("Properties", "Ctrl+F8")):
            if title in self.sections:
                self.act(wm, f"Go to {title}", lambda t=title: self.show_panel(t), key)

        hm = mb.addMenu("&Help")
        self.act(hm, "Quick tips", self.tips)
        self.act(hm, "About", self.about)

    # ------------------------------------------------------------------ windows
    def set_wireframe(self, on):
        VItem.wire = bool(on)
        self.v.viewport().update()

    def restore_layout(self):
        pass

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "d_side"):          # side panels follow the window size
            self.inner.resizeDocks([self.d_side], [ui.dock_width_for(self.width(), self.stack.minimumSizeHint().width())], Qt.Horizontal)

    # ------------------------------------------------------------------ tools
    def show_panel(self, title):
        """open a side panel, unfold it and scroll to it"""
        box = self.sections[title]
        self.d_side.show()
        box.setVisible(True)
        if getattr(box, "collapsed", False):
            box.set_collapsed(False)
        QTimer.singleShot(0, lambda: self.d_side.widget().ensureWidgetVisible(box, 0, 0))

    def use_tool(self, name):
        self.v.set_tool(name)
        self.tool_actions[name].setChecked(True)
        self._cur_opt = name
        for k, w in self.opt_widgets.items():
            w.setVisible(k == name)
        self.opt_none.setVisible(name not in self.opt_widgets)
        self.lbl_hint.setText(HINTS.get(name, ""))

    def on_tool_request(self, name):
        if name == "_style_changed":
            self.sync_panel()
            return
        self.use_tool(name)

    def node_op(self, op):
        if self.v.tool is not self.v.tools["node"]:
            self.use_tool("node")
        t = self.v.tools["node"]
        if t.item is None:
            self.statusBar().showMessage("Select a shape first", 3000)
            return
        getattr(t, op)()

    def text_opts_changed(self, *_):
        if self._busy:
            return
        self.v.text_opt.update({"family": self.o_font.currentFont().family(), "size": self.o_size.value() * 96.0 / 72.0,
                                "bold": self.o_bold.isChecked(), "italic": self.o_italic.isChecked()})
        self.v.tools["text"].restyle_selected()

    # ------------------------------------------------------------------ panels <-> selection
    def first_leaf(self):
        for it in self.v.selected():
            for lf in leaves_of(it):
                return lf
        return None

    def sync_panel(self):
        lf = self.first_leaf()
        fill = lf.fill if lf else self.v.fill
        st = lf.stroke if lf else self.v.stroke
        self._busy = True
        try:
            self.f_type.setCurrentIndex({"none": 0, "solid": 1, "linear": 2, "radial": 3}.get(fill["type"], 1))
            self.f_c1.set_color(fill["c1"]); self.f_c2.set_color(fill["c2"])
            self.f_angle.setValue(int(fill.get("angle", 0)))
            none = st["width"] <= 0 or st["color"] in (None, "none")
            self.s_none.setChecked(none)
            self.s_color.set_color(st["color"] if st["color"] not in (None, "none") else "#1B1040")
            self.s_width.setValue(max(st["width"], 0) / PX_PER_MM)
            self.s_dash.setCurrentText(st["dash"]); self.s_join.setCurrentText(st["join"]); self.s_cap.setCurrentText(st["cap"])
            sel = self.v.selected()
            self.s_op.setValue(int(round((sel[0].opacity() if sel else 1.0) * 100)))
            self.f_c2.setVisible(fill["type"] in ("linear", "radial"))
            self.f_angle.setEnabled(fill["type"] == "linear")
            self.palette.set_current(fill["c1"] if fill["type"] != "none" else None, st["color"] if not none else None)
            self.colour.set_colors(fill["c1"], st["color"] if st["color"] not in (None, "none") else "#000000")
        finally:
            self._busy = False

    def fill_changed(self, *_):
        if self._busy:
            return
        t = ["none", "solid", "linear", "radial"][self.f_type.currentIndex()]
        ops.set_style(self.v, fill={"type": t, "c1": self.f_c1.color, "c2": self.f_c2.color, "angle": self.f_angle.value()}, label="Fill")
        self.sync_panel()

    def stroke_changed(self, *_):
        if self._busy:
            return
        w = 0.0 if self.s_none.isChecked() else self.s_width.value() * PX_PER_MM
        if not self.s_none.isChecked() and w <= 0:
            w = 0.1 * PX_PER_MM
        ops.set_style(self.v, stroke={"color": self.s_color.color, "width": w, "dash": self.s_dash.currentText(),
                                      "join": self.s_join.currentText(), "cap": self.s_cap.currentText()}, label="Outline")
        self.sync_panel()

    def set_outline_color(self, c):
        if c is None:
            ops.set_style(self.v, stroke={"width": 0.0}, label="No outline")
        else:
            lf = self.first_leaf()
            cur = lf.stroke["width"] if lf else self.v.stroke["width"]
            ops.set_style(self.v, stroke={"color": c, "width": cur if cur > 0 else 1.0 * PX_PER_MM * 0.5}, label="Outline colour")
        self.sync_panel()

    def opacity_changed(self, val):
        if self._busy:
            return
        ops.set_style(self.v, opacity=val / 100.0, label="Opacity")

    def pick_color(self, btn, cb):
        c = QColorDialog.getColor(QColor(btn.color), self, "Colour")
        if c.isValid():
            btn.set_color(c.name())
            if btn is self.s_color:
                self.s_none.blockSignals(True); self.s_none.setChecked(False); self.s_none.blockSignals(False)
            cb()

    def colour_picked(self, target, col):
        if target == "fill":
            f = {"c1": col}
            lf = self.first_leaf()
            if (lf.fill if lf else self.v.fill)["type"] == "none":
                f["type"] = "solid"
            ops.set_style(self.v, fill=f, label="Fill colour")
        else:
            lf = self.first_leaf()
            cur = lf.stroke["width"] if lf else self.v.stroke["width"]
            ops.set_style(self.v, stroke={"color": col, "width": cur if cur > 0 else 1.0}, label="Outline colour")
        self.sync_panel()

    def trace_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Trace image", "", "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not p:
            return
        d = QDialog(self)
        d.setWindowTitle("Trace image")
        f = QFormLayout(d)
        mode = QComboBox(); mode.addItems(["Black and white (logo, scan)", "Colour (posterize)"])
        thr = QSlider(Qt.Horizontal); thr.setRange(20, 235); thr.setValue(128)
        cols = QSpinBox(); cols.setRange(2, 12); cols.setValue(5)
        detail = QComboBox(); detail.addItems(["Smooth (fewer points)", "Normal", "Detailed"]); detail.setCurrentIndex(1)
        sm = QCheckBox("Round the corners into curves"); sm.setChecked(True)
        inv = QCheckBox("Trace the light parts instead")
        f.addRow("Mode", mode); f.addRow("Threshold", thr); f.addRow("Colours", cols); f.addRow("Detail", detail); f.addRow("", sm); f.addRow("", inv)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        f.addRow(bb)
        if not d.exec():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            n = ops.trace_file(self.v, p, mode="bw" if mode.currentIndex() == 0 else "color", threshold=thr.value(), colors=cols.value(),
                               detail=[1.6, 1.0, 0.5][detail.currentIndex()], smooth=sm.isChecked(), invert=inv.isChecked())
        except Exception as e:                          # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Could not trace this picture:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if n:
            self.statusBar().showMessage(f"Traced into {n} shape layer(s) - use Ungroup to edit each colour", 6000)

    def geo_edit(self, which):
        if self._busy or not self.v.selected():
            return
        r = union_rect(self.v.selected())
        ratio = r.width() / r.height() if r.height() > 1e-9 else 1.0
        kw = {}
        if which == "x": kw["x"] = self.px.value()
        elif which == "y": kw["y"] = self.py.value()
        elif which == "w":
            kw["w"] = self.pw.value()
            if self.lock.isChecked(): kw["h"] = self.pw.value() / ratio
        else:
            kw["h"] = self.ph.value()
            if self.lock.isChecked(): kw["w"] = self.ph.value() * ratio
        ops.set_geometry(self.v, **kw)

    # ------------------------------------------------------------------ events
    def on_selection(self):
        s = self.v.selected()
        self._busy = True
        try:
            if s:
                r = union_rect(s)
                for w, val in ((self.px, r.x()), (self.py, r.y()), (self.pw, r.width()), (self.ph, r.height())):
                    w.setValue(val / PX_PER_MM)
            for w in (self.px, self.py, self.pw, self.ph):
                w.setEnabled(bool(s))
        finally:
            self._busy = False
        self.lbl_sel.setText(f"{len(s)} selected" if s else "Nothing selected")
        self.sync_panel()

    def on_doc(self):
        self.on_selection()
        name = os.path.basename(self.v.path) if self.v.path else "Untitled"
        self.setWindowTitle(f"{name}{' *' if self.v.dirty else ''} - {APP_NAME}")
        if 0 <= self._cur < len(self.docs) and not self._switching:
            rec = self.docs[self._cur]
            rec["name"] = name if self.v.path else rec["name"]
            self.dtabs.setTabText(self._cur, rec["name"] + (" *" if self.v.dirty else ""))
            self.dtabs.setTabToolTip(self._cur, self.v.path or rec["name"])

    def on_cursor(self, p):
        self.lbl_pos.setText(f"X {p.x() / PX_PER_MM:.1f}  Y {p.y() / PX_PER_MM:.1f} mm")

    def on_zoom(self, z):
        self.lbl_zoom.setText(f"{z * 100:.0f} %")

    def context_menu(self, pos):
        if self.v.tool_name not in ("select", "node"):
            return
        v = self.v
        m = QMenu(self)
        if self.v.tool_name == "select":
            p = v.mapToScene(pos)
            from vkit.tools import hit_item
            it = hit_item(v, p)
            if it is not None and not it.isSelected():
                v.scene_.clearSelection(); it.setSelected(True)
        for t, fn in (("Cut", lambda: ops.cut(v)), ("Copy", lambda: ops.copy_sel(v)), ("Paste", lambda: ops.paste(v)),
                      ("Duplicate", lambda: ops.duplicate(v)), ("Delete", lambda: ops.delete(v))):
            m.addAction(t, fn)
        m.addSeparator()
        m.addAction("Group", lambda: ops.group(v)); m.addAction("Ungroup", lambda: ops.ungroup(v))
        m.addAction("Combine", lambda: ops.combine(v)); m.addAction("Break apart", lambda: ops.break_apart(v))
        am = m.addMenu("Arrange")
        am.addAction("To front", lambda: ops.arrange(v, "front")); am.addAction("To back", lambda: ops.arrange(v, "back"))
        am.addAction("Forward one", lambda: ops.arrange(v, "forward")); am.addAction("Back one", lambda: ops.arrange(v, "backward"))
        m.addSeparator()
        m.addAction("Lock", lambda: ops.lock(v, True))
        m.exec(v.mapToGlobal(pos))

    # ------------------------------------------------------------------ files
    # ---- documents (tabs): one canvas, each tab keeps its own drawing, undo history and path
    def _rec_from_view(self):
        v = self.v
        return {"snap": v.snapshot(), "path": v.path, "dirty": v.dirty, "undo": list(v.undo_stack), "redo": list(v.redo_stack),
                "cur_layer": v.cur_layer}

    def _stash(self):
        if 0 <= self._cur < len(self.docs):
            keep = self.docs[self._cur]
            keep.update(self._rec_from_view())

    def _apply(self, rec):
        v = self.v
        self._switching = True
        try:
            data = json.loads(rec["snap"])
            v.scene_.blockSignals(True)
            v.scene_.clearSelection()
            for it in list(top_level(v.scene_)):
                v.scene_.removeItem(it)
            v.page, v.guides = load_into(v.scene_, data)
            v.layers = layers_from(data)
            v.cur_layer = rec.get("cur_layer") or v.layers[-1]["id"]
            if v.layer_index(v.cur_layer) < 0:
                v.cur_layer = v.layers[0]["id"]
            v.apply_layers()
            v.scene_.blockSignals(False)
            v.layersChanged.emit()
            v.undo_stack, v.redo_stack = list(rec["undo"]), list(rec["redo"])
            v._base = rec["snap"]
            v.dirty, v.path = rec["dirty"], rec["path"]
            v.fit_page()
            v.docChanged.emit()
            v.selectionChanged2.emit()
        finally:
            self._switching = False
        self.on_doc()

    def _add_tab(self, w=210.0, h=297.0):
        """a new empty drawing in a new tab"""
        self._stash()
        self._untitled += 1
        self.v.new_document(w, h)
        self.docs.append({"name": "Untitled" if self._untitled == 1 else f"Untitled-{self._untitled}"})
        self.docs[-1].update(self._rec_from_view())
        self._switching = True
        self._cur = len(self.docs) - 1
        self.dtabs.addTab("")
        self.dtabs.setCurrentIndex(self._cur)
        self._switching = False
        self.on_doc()

    def _tab_changed(self, i):
        if self._switching or i < 0 or i >= len(self.docs) or i == self._cur:
            return
        self._stash()
        self._cur = i
        self._apply(self.docs[i])

    def _confirm_close(self, i):
        """True if tab i may be closed (asks to save when it has changes)"""
        if i == self._cur:
            self._stash()
        rec = self.docs[i]
        if not rec["dirty"]:
            return True
        if i != self._cur:
            self.dtabs.setCurrentIndex(i)
        r = QMessageBox.question(self, APP_NAME, f"Save changes to {rec['name']}?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return bool(self.save())
        return r == QMessageBox.Discard

    def close_tab(self, i):
        if i < 0 or i >= len(self.docs) or not self._confirm_close(i):
            return
        if len(self.docs) == 1:                      # the last project: leave a fresh empty one
            self.docs.clear(); self._cur = -1
            self._switching = True
            self.dtabs.removeTab(0)
            self._switching = False
            self._untitled = 0
            self._add_tab()
            self.show_start()
            return
        self._switching = True
        self.docs.pop(i)
        self.dtabs.removeTab(i)
        self._switching = False
        self._cur = min(self.dtabs.currentIndex(), len(self.docs) - 1)
        self._apply(self.docs[self._cur])

    def maybe_save(self):
        """before the program closes: every project with changes gets asked"""
        for i in range(len(self.docs)):
            if not self._confirm_close(i):
                return False
        return True

    # ------------------------------------------------------------------ start screen
    def show_start(self):
        self.start.refresh()
        self.centre.setCurrentIndex(0)
        for k in ("save", "export", "print"):
            self.file_actions[k].setEnabled(False)

    def show_work(self):
        self.centre.setCurrentIndex(1)
        for k in ("save", "export", "print"):
            self.file_actions[k].setEnabled(True)

    def start_new(self):
        if self.centre.currentIndex() == 0:          # from the start screen: the empty A4 page that is ready
            self.show_work()
            QTimer.singleShot(0, self.v.fit_page)
        else:
            self.new_doc()

    def rail_export(self):
        m = QMenu(self)
        m.addAction("Save as …", self.save_as)
        m.addSeparator()
        for label, kind in (("SVG …", "svg"), ("PNG image …", "png"), ("PDF …", "pdf"), ("EPS (Illustrator, Inkscape) …", "eps"),
                            ("AI (Adobe Illustrator) …", "ai")):
            m.addAction(label, lambda k=kind: self.export(k))
        w = self.tools_bar.widgetForAction(self.file_actions["export"])
        m.exec(w.mapToGlobal(w.rect().topRight()) if w else self.cursor().pos())

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            if self.centre.currentIndex() == 0:
                self.start.set_dropping(True)

    def dragLeaveEvent(self, ev):
        self.start.set_dropping(False)

    def dropEvent(self, ev):
        self.start.set_dropping(False)
        for u in ev.mimeData().urls():
            if u.isLocalFile() and u.toLocalFile().lower().endswith((".dvec", ".pvec", ".svg", ".ai", ".eps", ".pdf")):
                self.open_path(u.toLocalFile())

    def new_doc(self):
        d = PageDialog(self, "New drawing")
        if d.exec():
            pristine = len(self.docs) == 1 and not self.v.dirty and not self.v.path and self.centre.currentIndex() == 0
            if pristine:                                   # replace the unused start page instead of adding a tab
                self.v.new_document(d.w.value(), d.h.value())
                self.docs[0].update(self._rec_from_view())
                self.on_doc()
            else:
                self._add_tab(d.w.value(), d.h.value())
            self.show_work()

    # ------------------------------------------------------------------ artboards
    def _panel_artboards(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(6, 4, 6, 4)
        self.ab_list = QListWidget()
        self.ab_list.setMaximumHeight(130)
        self.ab_list.currentRowChanged.connect(self._ab_pick)
        self.ab_list.itemDoubleClicked.connect(lambda _it: self._ab_rename())
        l.addWidget(self.ab_list)
        row = QHBoxLayout()
        for t, fn, tip in (("+", self.add_artboard, "New artboard (same size, to the right)"), ("Copy", self.duplicate_artboard, "Duplicate the artboard with its artwork"),
                           ("Rename", self._ab_rename, "Rename"), ("Size…", self.page_setup, "Size of the active artboard"),
                           ("✕", self.delete_artboard, "Delete the artboard (the artwork stays)")):
            b = QPushButton(t); b.setToolTip(tip); b.clicked.connect(fn); row.addWidget(b)
        l.addLayout(row)
        tip = QLabel("Shift+O: draw a new artboard, or drag one to move it with its artwork.")
        tip.setWordWrap(True); tip.setStyleSheet("color:#6A7A72;")
        l.addWidget(tip)
        self.v.layersChanged.connect(self._ab_fill)
        self.v.docChanged.connect(self._ab_fill)
        self._ab_fill()
        return w

    def _ab_fill(self):
        if not hasattr(self, "ab_list"):
            return
        pg = self.v.page
        self.ab_list.blockSignals(True)
        self.ab_list.clear()
        for b in pg.boards:
            self.ab_list.addItem(f"{b['name']}   {b['w']:g} × {b['h']:g} mm")
        self.ab_list.setCurrentRow(pg.active)
        self.ab_list.blockSignals(False)

    def _ab_pick(self, row):
        if 0 <= row < len(self.v.page.boards):
            self.v.page.active = row
            self.v.fit_page()
            self.v.viewport().update()

    def add_artboard(self):
        self.v.page.add()
        self.v.commit("New artboard")
        self._ab_fill(); self.v.fit_page()

    def duplicate_artboard(self):
        v = self.v
        src = v.page.rect
        v.page.add()
        d = v.page.rect.topLeft() - src.topLeft()
        from vkit.model import item_from_json
        T = QTransform.fromTranslate(d.x(), d.y())
        for it in [i for i in top_level(v.scene_) if src.contains(geom_rect(i).center())]:
            n = item_from_json(it.to_json()); n.bake(T); v.scene_.addItem(n)
        ops.normalize_z(v)
        v.commit("Duplicate artboard")
        self._ab_fill(); v.fit_page()

    def delete_artboard(self):
        pg = self.v.page
        if len(pg.boards) < 2:
            self.statusBar().showMessage("A document keeps at least one artboard", 3000)
            return
        del pg.boards[pg.active]
        pg.active = max(0, pg.active - 1)
        self.v.commit("Delete artboard")
        self._ab_fill(); self.v.viewport().update()

    def _ab_rename(self):
        b = self.v.page.board
        n, ok = QInputDialog.getText(self, "Artboard", "Name:", text=b["name"])
        if ok and n.strip():
            b["name"] = n.strip()
            self.v.commit("Rename artboard")
            self._ab_fill(); self.v.viewport().update()

    def export_artboards(self):
        """every artboard: one PDF with a page each, or one PNG / SVG file each"""
        v = self.v
        kind, ok = QInputDialog.getItem(self, "Export all artboards", "As:", ["PDF (one page per artboard)", "PNG (one file each)", "SVG (one file each)"], 0, False)
        if not ok:
            return
        base = os.path.splitext(os.path.basename(v.path))[0] if v.path else "drawing"
        keep = v.page.active
        try:
            if kind.startswith("PDF"):
                p, _ = QFileDialog.getSaveFileName(self, "Export all artboards", f"{base}.pdf", "PDF (*.pdf)")
                if not p:
                    return
                wr = QPdfWriter(p if p.lower().endswith(".pdf") else p + ".pdf")
                wr.setResolution(300)
                wr.setPageMargins(QMarginsF(0, 0, 0, 0))
                pt = None
                for i in range(len(v.page.boards)):
                    v.page.active = i
                    wr.setPageSize(QPageSize(QSizeF(v.page.w_mm, v.page.h_mm), QPageSize.Millimeter))
                    if pt is None:
                        pt = QPainter(wr); pt.setRenderHint(QPainter.Antialiasing)
                    else:
                        wr.newPage()
                    self._render_scene(pt, QRectF(0, 0, wr.width(), wr.height()))
                pt.end()
                done = p
            else:
                folder = QFileDialog.getExistingDirectory(self, "Folder for the artboards")
                if not folder:
                    return
                dpi = 150
                if kind.startswith("PNG"):
                    dpi, ok = QInputDialog.getInt(self, "PNG", "Resolution (dpi):", 150, 20, 1200)
                    if not ok:
                        return
                for i, b in enumerate(v.page.boards):
                    v.page.active = i
                    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in b["name"]).strip() or f"artboard {i + 1}"
                    if kind.startswith("PNG"):
                        self.render_png(os.path.join(folder, f"{base} - {safe}.png"), dpi, False)
                    else:
                        with open(os.path.join(folder, f"{base} - {safe}.svg"), "w", encoding="utf-8") as fh:
                            fh.write(export_svg(v.scene_, v.page, None, v.layers))
                done = folder
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Could not export:\n{e}")
            return
        finally:
            v.page.active = keep
            v.viewport().update()
        self.statusBar().showMessage(f"Exported {len(v.page.boards)} artboards to {done}", 6000)

    def page_setup(self):
        d = PageDialog(self, "Page size", self.v.page.w_mm, self.v.page.h_mm)
        if d.exec():
            self.v.page.w_mm, self.v.page.h_mm = d.w.value(), d.h.value()
            self.v.commit("Page size")
            self._ab_fill()
            self.v.fit_page()

    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open", "", "Digonto Vector and others (*.dvec *.svg *.ai *.eps *.pdf)")
        if p:
            self.open_path(p)

    def open_path(self, p):
        self._open_path(p)
        self.show_work()
        fams = [it.meta.get("family") for it in self.v.scene_.items()
                if isinstance(it, VItem) and getattr(it, "kind", "") == "text" and getattr(it, "meta", None)]
        if any(not ui.font_installed(x) for x in fams):
            QTimer.singleShot(150, lambda: ui.note_missing_fonts(self, fams))

    def _open_path(self, p):
        from vkit.model import load_into
        for k, rec in enumerate(self.docs):               # already open? just show it
            if os.path.abspath(rec.get("path") or "") == os.path.abspath(p) and rec.get("path"):
                self.dtabs.setCurrentIndex(k)
                return
        pristine = self.docs and not self.v.dirty and not self.v.path and not self.v.undo_stack
        if not pristine:
            self._add_tab()
        try:
            if p.lower().endswith((".ai", ".eps", ".pdf")):
                self.v.new_document()
                page, n, note = self._import_vector_file(p)
                self.v.page = page
                self.v.commit_base()
                self.v.dirty = False
                self.v.path = None
                self.v.fit_page()
                self.v.docChanged.emit()
                self.statusBar().showMessage(f"Imported {n} shapes. {note} Use Save as to keep it.", 9000)
                return
            if p.lower().endswith(".svg"):
                self.v.new_document()
                with open(p, encoding="utf-8", errors="replace") as f:
                    page, n, lays = import_svg(f.read(), self.v.scene_, with_layers=True)
                self.v.page = page
                if lays:                                  # Inkscape / Illustrator layers
                    self.v.layers = lays
                    self.v.cur_layer = lays[-1]["id"]
                    self.v.normalize_z()
                    self.v.apply_layers()
                    self.v.layersChanged.emit()
                self.v.commit_base()
                self.v.dirty = False
                self.v.path = None
                self.v.fit_page()
                self.v.docChanged.emit()
                self.statusBar().showMessage(f"Imported {n} shapes from SVG - use Save as to keep it", 6000)
                return
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            self.v.scene_.blockSignals(True)
            self.v.page, self.v.guides = load_into(self.v.scene_, data)
            self.v.layers = layers_from(data)
            self.v.cur_layer = self.v.layers[-1]["id"]
            self.v.apply_layers()
            self.v.layersChanged.emit()
            self.v.scene_.blockSignals(False)
            self.v.undo_stack.clear(); self.v.redo_stack.clear()
            self.v.commit_base()
            self.v.dirty = False
            self.v.path = p
            self.v.fit_page()
            self.v.docChanged.emit()
            add_recent(p)
        except Exception as e:                      # noqa: BLE001 - show any reading problem to the user
            QMessageBox.warning(self, APP_NAME, f"Could not open this file:\n{e}")

    def save(self):
        if not self.v.path:
            return self.save_as()
        return self._write(self.v.path)

    def save_as(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save", "drawing.dvec", "Digonto Vector (*.dvec)")
        if not p:
            return False
        if not p.lower().endswith(".dvec"):
            p += ".dvec"
        return self._write(p)

    def _write(self, p):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.v.snapshot())
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Could not save:\n{e}")
            return False
        self.v.path = p
        self.v.dirty = False
        add_recent(p)
        self.on_doc()
        self.statusBar().showMessage("Saved " + p, 4000)
        return True

    def _import_vector_file(self, p):
        """AI / EPS / PDF with a small progress window, so the program never looks frozen"""
        from vkit import epsai
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Opening " + os.path.basename(p) + "…", None, 0, 0, self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setMinimumDuration(300)
        dlg.setWindowModality(Qt.ApplicationModal)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        def step(text):
            dlg.setLabelText(text)
            QApplication.processEvents()
        try:
            return epsai.import_file(p, self.v.scene_, progress=step)
        finally:
            QApplication.restoreOverrideCursor()
            dlg.close()

    def import_svg_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Import", "", "Vector files (*.svg *.ai *.eps *.pdf)")
        if not p:
            return
        before = set(top_level(self.v.scene_))
        note = ""
        try:
            if p.lower().endswith(".svg"):
                with open(p, encoding="utf-8", errors="replace") as f:
                    page, n, lays = import_svg(f.read(), self.v.scene_, with_layers=True)
                if lays:                                  # the file's layers are added above the current ones
                    ids = {}
                    for lay in lays:
                        nid = self.v.new_layer_id()
                        ids[lay["id"]] = nid
                        self.v.layers.append(dict(lay, id=nid))
                    for it in top_level(self.v.scene_):
                        if it not in before and it.layer in ids:
                            it.layer = ids[it.layer]
                    self.v.layersChanged.emit()
            else:
                page, n, note = self._import_vector_file(p)
        except Exception as e:                      # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Could not read this file:\n{e}")
            return
        new = [i for i in top_level(self.v.scene_) if i not in before]
        if not before:
            self.v.page = page
            self.v.fit_page()
        ops.normalize_z(self.v)
        ops.select(self.v, new)
        self.v.commit("Import SVG")
        self.statusBar().showMessage(f"Imported {n} shapes. {note}", 8000)

    def export(self, kind, selection=False):
        v = self.v
        items = ops.sel(v) if selection else None
        if selection and not items:
            self.statusBar().showMessage("Select something first", 3000)
            return
        base = os.path.splitext(os.path.basename(v.path))[0] if v.path else "drawing"
        ext = {"svg": "SVG (*.svg)", "png": "PNG image (*.png)", "pdf": "PDF (*.pdf)", "eps": "EPS (*.eps)", "ai": "Adobe Illustrator (*.ai)"}[kind]
        p, _ = QFileDialog.getSaveFileName(self, "Export", f"{base}.{kind}", ext)
        if not p:
            return
        if not p.lower().endswith("." + kind):
            p += "." + kind
        try:
            if kind == "svg":
                with open(p, "w", encoding="utf-8") as f:
                    f.write(export_svg(v.scene_, v.page, items, None if items else v.layers))
            elif kind == "png":
                dpi, ok = QInputDialog.getInt(self, "PNG", "Resolution (dpi):", 150, 20, 1200)
                if not ok:
                    return
                transparent = QMessageBox.question(self, "PNG", "Transparent background?") == QMessageBox.Yes
                self.render_png(p, dpi, transparent)
            elif kind == "eps":
                from vkit import epsai
                epsai.export_eps(v.scene_, v.page, p, items)
            else:                                   # pdf, and ai (a PDF-compatible file that Illustrator opens)
                self.render_pdf(p)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Could not export:\n{e}")
            return
        self.statusBar().showMessage("Exported " + p, 5000)

    def _render_scene(self, painter, target):
        sel = v_sel = self.v.selected()
        self.v.scene_.blockSignals(True)
        self.v.scene_.clearSelection()
        self.v.scene_.render(painter, target, self.v.page.rect)
        for it in v_sel:
            it.setSelected(True)
        self.v.scene_.blockSignals(False)

    def render_png(self, p, dpi, transparent):
        w = max(1, round(self.v.page.w_mm / 25.4 * dpi)); h = max(1, round(self.v.page.h_mm / 25.4 * dpi))
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(Qt.transparent if transparent else QColor("white"))
        pt = QPainter(img)
        pt.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self._render_scene(pt, QRectF(0, 0, w, h))
        pt.end()
        img.setDotsPerMeterX(int(dpi / 0.0254)); img.setDotsPerMeterY(int(dpi / 0.0254))
        if not img.save(p):
            raise OSError("could not write the image")

    def print_doc(self):
        pw, ph = self.v.page.w_mm, self.v.page.h_mm

        def page(i, p, rect):
            p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
            self._render_scene(p, ui.fit_rect(pw, ph, rect))
        ui.print_pages(self, 1, page, os.path.basename(self.v.path) if self.v.path else "Digonto Vector")

    def render_pdf(self, p):
        wr = QPdfWriter(p)
        wr.setResolution(300)
        wr.setPageSize(QPageSize(QSizeF(self.v.page.w_mm, self.v.page.h_mm), QPageSize.Millimeter))
        wr.setPageMargins(QMarginsF(0, 0, 0, 0))
        pt = QPainter(wr)
        pt.setRenderHint(QPainter.Antialiasing)
        self._render_scene(pt, QRectF(0, 0, wr.width(), wr.height()))
        pt.end()

    # ------------------------------------------------------------------ misc
    def grid_dialog(self):
        x, ok = QInputDialog.getDouble(self, "Grid", "Grid spacing (mm):", self.v.grid["mm"], 0.5, 500, 1)
        if ok:
            self.v.grid["mm"] = x
            self.v.viewport().update()

    def clear_guides(self):
        self.v.guides = {"h": [], "v": []}
        self.v.commit("Clear guides")

    def tips(self):
        QMessageBox.information(self, "Quick tips",
                                "• Colours: left-click a colour at the bottom for the fill, right-click for the outline.\n"
                                "• Drag from a ruler to pull out a guide; objects snap to guides, other objects and the page.\n"
                                "• Pathfinder (Unite, Minus front, Intersect, Exclude, Divide) makes new shapes from overlapping ones.\n"
                                "• Alt + drag leaves a copy behind; Ctrl+D repeats the last move, rotate or scale (with or without copy).\n"
                                "• Double-click a shape to edit its nodes. Double-click a line in node mode to add a node.\n"
                                "• Hold Space and drag to move the view; mouse wheel zooms; Shift+wheel scrolls sideways.\n"
                                "• Shortcuts are the same as Illustrator: V select, A direct select, P pen, M rectangle, L ellipse, T text, Ctrl+G group, Ctrl+8 compound path, Ctrl+2 lock, Ctrl+Y outline view.\n"
                                "• Text can be edited until you use 'Text → curves'.")

    def about(self):
        QMessageBox.about(self, "About " + APP_NAME,
                          f"<h3>{APP_NAME} {VERSION}</h3><p>Draw logos, shapes and illustrations with Illustrator-style tools and shortcuts in the Digonto design.</p>"
                          "<p>Files: .dvec (own format). Import SVG, AI, EPS and PDF. Export SVG, EPS, AI, PDF and PNG.</p><p>For Inkscape and Illustrator use SVG: layers, groups and editable text are kept both ways.</p>")

    def closeEvent(self, ev):
        if not self.maybe_save():
            ev.ignore()
            return
        from PySide6.QtCore import QSettings
        QSettings("Digonto", "Vector").setValue("state", self.saveState())
        ev.accept()
        QTimer.singleShot(1500, lambda: os._exit(0))


def main():
    if "--version" in sys.argv:
        print(VERSION)
        return
    ui.mac_app_name(APP_NAME)
    app = QApplication(sys.argv)
    if ui.forward_to_running(SLUG, sys.argv[1:]):
        os._exit(0)
    ui.setup_app(app, APP_NAME, SLUG, ui.app_icon(HERE, SLUG))
    path = next((a for a in sys.argv[1:] if os.path.isfile(a)), None)
    w = Main(path)
    w.show()
    ui.serve_single(SLUG, w, w.open_path)
    rc = app.exec()
    os._exit(rc)


if __name__ == "__main__":
    main()
