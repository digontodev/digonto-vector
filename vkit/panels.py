"""Side panels: Navigator, Colour, History, Layers (the same idea as Digonto Images)."""
import colorsys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QGridLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
                               QScrollArea, QSlider, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .model import VGroup, VItem, top_level
from .widgets import build_palette


class ArrowScroll(QWidget):
    """a scroll area with an up arrow on top and a down arrow below it, next to the usual bar"""

    def __init__(self, inner):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.sa = QScrollArea()
        self.sa.setWidgetResizable(True)
        self.sa.setFrameShape(QScrollArea.NoFrame)
        self.sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sa.setWidget(inner)
        self.sa.verticalScrollBar().setStyleSheet("QScrollBar:vertical{width:14px;}")
        self.up, self.down = QPushButton("▲"), QPushButton("▼")
        for b, d in ((self.up, -1), (self.down, 1)):
            b.setFixedHeight(22)
            b.setAutoRepeat(True)
            b.setAutoRepeatInterval(40)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, d=d: self.sa.verticalScrollBar().setValue(self.sa.verticalScrollBar().value() + d * 40))
        v.addWidget(self.up)
        v.addWidget(self.sa, 1)
        v.addWidget(self.down)
        self.setMinimumSize(150, 120)
        bar = self.sa.verticalScrollBar()
        bar.rangeChanged.connect(lambda lo, hi: (self.up.setVisible(hi > lo), self.down.setVisible(hi > lo)))
        self.up.hide()
        self.down.hide()


# ------------------------------------------------------------------ navigator
class NavView(QWidget):
    def __init__(self, view):
        super().__init__()
        self.v = view
        self.setMinimumHeight(120)
        self.setMinimumWidth(120)
        self._img = None
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(250)
        self._t.timeout.connect(self._rebuild)
        view.docChanged.connect(self.refresh)
        view.layersChanged.connect(self.refresh)
        view.zoomChanged.connect(lambda _: self.update())
        view.horizontalScrollBar().valueChanged.connect(lambda _: self.update())
        view.verticalScrollBar().valueChanged.connect(lambda _: self.update())

    def refresh(self):
        self._t.start()

    def _geom(self):
        pr = self.v.page.rect
        m = 6
        s = min((self.width() - 2 * m) / pr.width(), (self.height() - 2 * m) / pr.height())
        w, h = pr.width() * s, pr.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), s

    def _rebuild(self):
        r, s = self._geom()
        if r.width() < 4 or r.height() < 4:
            return
        img = QImage(int(r.width()), int(r.height()), QImage.Format_ARGB32)
        img.fill(Qt.white)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        sel = self.v.selected()
        self.v.scene_.blockSignals(True)
        self.v.scene_.clearSelection()
        self.v.scene_.render(p, QRectF(0, 0, img.width(), img.height()), self.v.page.rect)
        for it in sel:
            it.setSelected(True)
        self.v.scene_.blockSignals(False)
        p.end()
        self._img = img
        self.update()

    def resizeEvent(self, e):
        self.refresh()

    def showEvent(self, e):
        self.refresh()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#4D4950"))
        r, s = self._geom()
        if self._img is not None:
            p.drawImage(r.topLeft(), self._img)
        else:
            p.fillRect(r, Qt.white)
        vr = self.v.mapToScene(self.v.viewport().rect()).boundingRect()
        pr = self.v.page.rect
        f = QRectF(r.x() + (vr.x() - pr.x()) * s, r.y() + (vr.y() - pr.y()) * s, vr.width() * s, vr.height() * s)
        p.setClipRect(self.rect())
        p.setPen(QPen(QColor(255, 40, 40), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(f)

    def _go(self, pos):
        r, s = self._geom()
        pr = self.v.page.rect
        self.v.centerOn(QPointF(pr.x() + (pos.x() - r.x()) / s, pr.y() + (pos.y() - r.y()) / s))

    def mousePressEvent(self, e):
        self._go(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._go(e.position())


class Navigator(QWidget):
    def __init__(self, view):
        super().__init__()
        self.v = view
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(NavView(view))
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 4)
        self.lbl = QLabel("100%")
        for t, fn in (("−", lambda: view.zoom_to(view.zoom_factor() / 1.25)), ("+", lambda: view.zoom_to(view.zoom_factor() * 1.25))):
            b = QToolButton(); b.setText(t); b.clicked.connect(fn); row.addWidget(b)
        row.addWidget(self.lbl, 1)
        for t, fn in (("Fit", view.fit_page), ("1:1", lambda: view.zoom_to(1.0))):
            b = QToolButton(); b.setText(t); b.clicked.connect(fn); row.addWidget(b)
        l.addLayout(row)
        view.zoomChanged.connect(lambda z: self.lbl.setText(f"{z * 100:.0f}%"))


# ------------------------------------------------------------------ colour
class ColourPanel(QWidget):
    colourPicked = Signal(str, str)         # target ("fill" / "stroke"), colour

    def __init__(self):
        super().__init__()
        self.target = "fill"
        self.fill, self.stroke = "#C2395C", "#1B1040"
        self._busy = False
        self.recent = []
        l = QVBoxLayout(self)
        l.setContentsMargins(6, 4, 6, 4)
        row = QHBoxLayout()
        self.bf, self.bs = QPushButton("Fill"), QPushButton("Outline")
        for b, t in ((self.bf, "fill"), (self.bs, "stroke")):
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, t=t: self.set_target(t))
            row.addWidget(b)
        self.bf.setChecked(True)
        l.addLayout(row)
        self.swatch = QFrame(); self.swatch.setFixedHeight(26)
        l.addWidget(self.swatch)
        self.sl = {}
        for k, mx, col in (("R", 255, "#E04040"), ("G", 255, "#30A050"), ("B", 255, "#3060E0")):
            r = QHBoxLayout()
            lb = QLabel(k); lb.setFixedWidth(14)
            s = QSlider(Qt.Horizontal); s.setRange(0, mx)
            s.valueChanged.connect(self._rgb)
            self.sl[k] = s
            r.addWidget(lb); r.addWidget(s)
            l.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("Hex"))
        self.hex = QLineEdit(); self.hex.setMaxLength(7)
        self.hex.editingFinished.connect(self._hex)
        r.addWidget(self.hex)
        l.addLayout(r)
        l.addWidget(QLabel("Swatches"))
        self.grid = QWidget()
        self.grid.setFixedHeight(5 * 17 + 4)
        self.grid.paintEvent = self._paint_grid
        self.grid.mousePressEvent = self._grid_click
        self.colors = build_palette()
        l.addWidget(self.grid)
        l.addWidget(QLabel("Recent"))
        self.rec = QWidget(); self.rec.setFixedHeight(22)
        self.rec.paintEvent = self._paint_recent
        self.rec.mousePressEvent = self._recent_click
        l.addWidget(self.rec)
        self.refresh()

    def set_target(self, t):
        self.target = t
        self.bf.setChecked(t == "fill"); self.bs.setChecked(t == "stroke")
        self.refresh()

    def set_colors(self, fill, stroke):
        self.fill = fill or "#FFFFFF"
        self.stroke = stroke or "#000000"
        self.refresh()

    def cur(self):
        return self.fill if self.target == "fill" else self.stroke

    def refresh(self):
        c = QColor(self.cur())
        self._busy = True
        self.swatch.setStyleSheet(f"background:{c.name()};border:1px solid #736A7A;")
        self.sl["R"].setValue(c.red()); self.sl["G"].setValue(c.green()); self.sl["B"].setValue(c.blue())
        self.hex.setText(c.name().upper())
        self._busy = False

    def _emit(self, col):
        if self.target == "fill":
            self.fill = col
        else:
            self.stroke = col
        self.refresh()
        self.colourPicked.emit(self.target, col)

    def _rgb(self, _):
        if self._busy:
            return
        self._emit(QColor(self.sl["R"].value(), self.sl["G"].value(), self.sl["B"].value()).name())

    def _hex(self):
        c = QColor(self.hex.text() if self.hex.text().startswith("#") else "#" + self.hex.text())
        if c.isValid():
            self._emit(c.name())
            self.add_recent(c.name())

    def add_recent(self, c):
        c = c.upper()
        if c in self.recent:
            self.recent.remove(c)
        self.recent.insert(0, c)
        self.recent = self.recent[:12]
        self.rec.update()

    def _cell(self, i):
        col, row = divmod(i, 5)
        return QRectF(col * 17 + 1, row * 17 + 1, 16, 16)

    def _paint_grid(self, e):
        p = QPainter(self.grid)
        p.setPen(QPen(QColor(0, 0, 0, 50), 1))
        for i, c in enumerate(self.colors):
            r = self._cell(i)
            if r.right() > self.grid.width():
                break
            p.setBrush(QColor(c)); p.drawRect(r)

    def _grid_click(self, e):
        for i, c in enumerate(self.colors):
            if self._cell(i).contains(e.position()):
                self._emit(c)
                self.add_recent(c)
                return

    def _paint_recent(self, e):
        p = QPainter(self.rec)
        p.setPen(QPen(QColor(0, 0, 0, 50), 1))
        for i, c in enumerate(self.recent):
            p.setBrush(QColor(c)); p.drawRect(QRectF(i * 20 + 1, 1, 18, 18))

    def _recent_click(self, e):
        i = int(e.position().x() // 20)
        if 0 <= i < len(self.recent):
            self._emit(self.recent[i])


# ------------------------------------------------------------------ history
class HistoryPanel(QWidget):
    def __init__(self, view):
        super().__init__()
        self.v = view
        self._busy = False
        l = QVBoxLayout(self)
        l.setContentsMargins(6, 6, 6, 6)
        self.list = QListWidget()
        self.list.setMinimumHeight(140)
        self.list.currentRowChanged.connect(self._go)
        l.addWidget(self.list)
        view.docChanged.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        v = self.v
        self._busy = True
        self.list.clear()
        self.list.addItem("Start")
        for _, label in v.undo_stack:
            self.list.addItem(label)
        pos = len(v.undo_stack)
        for _, label in reversed(v.redo_stack):
            self.list.addItem(label)
        self.list.setCurrentRow(pos)
        for i in range(self.list.count()):
            if i > pos:
                self.list.item(i).setForeground(QColor("#999"))
        self._busy = False

    def _go(self, row):
        if self._busy or row < 0:
            return
        v = self.v
        while len(v.undo_stack) > row:
            v.undo()
        while len(v.undo_stack) < row and v.redo_stack:
            v.redo()


# ------------------------------------------------------------------ layers
class LayersPanel(QWidget):
    def __init__(self, view):
        super().__init__()
        self.v = view
        self._busy = False
        l = QVBoxLayout(self)
        l.setContentsMargins(6, 4, 6, 4)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Show", "Lock"])
        self.tree.setRootIsDecorated(True)
        self.tree.setMinimumHeight(190)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 44); self.tree.setColumnWidth(2, 40)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.itemChanged.connect(self._changed)
        self.tree.itemDoubleClicked.connect(self._rename)
        l.addWidget(self.tree)
        g = QGridLayout()
        for k, (t, tip, fn) in enumerate((("New", "Add a layer", self.add_layer), ("Delete", "Delete the layer and what is on it", self.del_layer),
                           ("▲", "Move the layer up", lambda: self.move(1)), ("▼", "Move the layer down", lambda: self.move(-1)),
                           ("Move here", "Move the selected objects to this layer", self.move_here))):
            b = QPushButton(t); b.setToolTip(tip); b.clicked.connect(fn); g.addWidget(b, k // 3, k % 3)
        l.addLayout(g)
        view.docChanged.connect(lambda: QTimer.singleShot(0, self.rebuild))
        view.layersChanged.connect(lambda: QTimer.singleShot(0, self.rebuild))
        view.selectionChanged2.connect(self.sync_selection)
        self._items = {}
        self.rebuild()

    def rebuild(self):
        v = self.v
        self._busy = True
        self.tree.clear()
        self._items = {}
        sel = set(v.selected())
        for lay in reversed(v.layers):
            li = QTreeWidgetItem([lay["name"], "", ""])
            li.setData(0, Qt.UserRole, ("layer", lay["id"]))
            li.setFlags(li.flags() | Qt.ItemIsUserCheckable)
            li.setCheckState(1, Qt.Checked if lay["visible"] else Qt.Unchecked)
            li.setCheckState(2, Qt.Checked if lay["locked"] else Qt.Unchecked)
            f = li.font(0); f.setBold(True); li.setFont(0, f)
            if lay["id"] == v.cur_layer:
                li.setBackground(0, QColor("#E3D2F0"))
            self.tree.addTopLevelItem(li)
            objs = [i for i in reversed(top_level(v.scene_)) if i.layer == lay["id"]]
            for it in objs:
                if isinstance(it, VGroup):
                    label = it.name or f"Group ({len(it.leaves())})"
                elif it.kind == "text":
                    label = it.name or "Text: " + it.meta.get("text", "").split("\n")[0][:20]
                else:
                    label = it.name or "Shape"
                ci = QTreeWidgetItem([label, "", ""])
                ci.setData(0, Qt.UserRole, ("obj", id(it)))
                ci.setFlags(ci.flags() | Qt.ItemIsUserCheckable)
                ci.setCheckState(1, Qt.Unchecked if it.hidden else Qt.Checked)
                ci.setCheckState(2, Qt.Checked if it.locked else Qt.Unchecked)
                li.addChild(ci)
                self._items[id(it)] = (it, ci)
                if it in sel:
                    ci.setSelected(True)
            li.setExpanded(True)
        self._busy = False

    def sync_selection(self):
        if self._busy:
            return
        self._busy = True
        sel = set(self.v.selected())
        self.tree.clearSelection()
        for k, (it, ci) in self._items.items():
            if it in sel:
                ci.setSelected(True)
        self._busy = False

    def _selected(self):
        if self._busy:
            return
        v = self.v
        objs = []
        for ti in self.tree.selectedItems():
            kind, key = ti.data(0, Qt.UserRole)
            if kind == "layer":
                if v.layer_index(key) >= 0:
                    v.cur_layer = key
            elif key in self._items:
                objs.append(self._items[key][0])
                v.cur_layer = self._items[key][0].layer
        v.scene_.blockSignals(True)
        v.scene_.clearSelection()
        for it in objs:
            if it.isVisible() and not it.is_locked():
                it.setSelected(True)
        v.scene_.blockSignals(False)
        v.selectionChanged2.emit()
        v.viewport().update()
        QTimer.singleShot(0, self._mark_current)

    def _mark_current(self):
        self._busy = True
        for i in range(self.tree.topLevelItemCount()):
            li = self.tree.topLevelItem(i)
            li.setBackground(0, QColor("#E3D2F0") if li.data(0, Qt.UserRole)[1] == self.v.cur_layer else QColor(0, 0, 0, 0))
        self._busy = False

    def _changed(self, item, col):
        if self._busy or col not in (1, 2):
            return
        v = self.v
        kind, key = item.data(0, Qt.UserRole)
        on = item.checkState(col) == Qt.Checked
        if kind == "layer":
            k = v.layer_index(key)
            v.layers[k]["visible" if col == 1 else "locked"] = on if col == 2 else on
        elif key in self._items:
            it = self._items[key][0]
            if col == 1:
                it.hidden = not on
            else:
                it.locked = on
        v.commit("Layer visibility" if col == 1 else "Lock")

    def _rename(self, item, col):
        if col != 0:
            return
        kind, key = item.data(0, Qt.UserRole)
        if kind == "layer":
            k = self.v.layer_index(key)
            t, ok = QInputDialog.getText(self, "Rename layer", "Name:", text=self.v.layers[k]["name"])
            if ok and t.strip():
                self.v.layers[k]["name"] = t.strip()
                self.v.commit("Rename layer")
        elif key in self._items:
            it = self._items[key][0]
            t, ok = QInputDialog.getText(self, "Rename", "Name:", text=it.name)
            if ok:
                it.name = t.strip()
                self.v.commit("Rename")

    def add_layer(self):
        v = self.v
        n = len(v.layers) + 1
        lid = v.new_layer_id()
        k = v.layer_index(v.cur_layer)
        v.layers.insert(k + 1, {"id": lid, "name": f"Layer {n}", "visible": True, "locked": False})
        v.cur_layer = lid
        v.commit("New layer")

    def del_layer(self):
        v = self.v
        if len(v.layers) < 2:
            QMessageBox.information(self, "Layers", "A drawing needs at least one layer.")
            return
        k = v.layer_index(v.cur_layer)
        has = [i for i in top_level(v.scene_) if i.layer == v.cur_layer]
        if has and QMessageBox.question(self, "Delete layer", f"Delete this layer and its {len(has)} object(s)?") != QMessageBox.Yes:
            return
        for it in has:
            v.scene_.removeItem(it)
        v.layers.pop(k)
        v.cur_layer = v.layers[min(k, len(v.layers) - 1)]["id"]
        v.commit("Delete layer")

    def move(self, d):
        v = self.v
        k = v.layer_index(v.cur_layer)
        j = k + d
        if 0 <= j < len(v.layers):
            v.layers[k], v.layers[j] = v.layers[j], v.layers[k]
            v.commit("Move layer")

    def move_here(self):
        v = self.v
        s = v.selected()
        if not s:
            return
        for it in s:
            it.layer = v.cur_layer
            it.setZValue(1e9)
        v.commit("Move to layer")
