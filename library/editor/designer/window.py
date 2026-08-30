# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Main window: theme picker + element tree | preview canvas + problem bar |
inspector. Owns the theme document and the render loop; the canvas and
inspector are dumb views over it."""

import time
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QFrame, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QSplitter, QStatusBar, QToolBar,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from library.editor import renderer
from library.editor.designer import model, style
from library.editor.designer.canvas import DesignerCanvas
from library.editor.designer.inspector import Inspector

ElementId = Tuple[str, ...]
BBox = Tuple[int, int, int, int]

AUTO_REFRESH_MS = 1000

_SEVERITY_LABEL = {
    "conflict": "CONFLICT",
    "warning": "OVERLAP",
    "notice": "OVERLAP",
}
_SEVERITY_COLOR = {
    "conflict": style.SEV_CONFLICT,
    "warning": style.SEV_WARNING,
    "notice": style.SEV_NOTICE,
    "error": style.SEV_ERROR,
}


class DesignerWindow(QMainWindow):

    def __init__(self, initial_theme: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Theme Designer")
        self.setMinimumSize(QSize(1100, 700))
        self.resize(1440, 900)

        # Document state
        self._theme_name: Optional[str] = None
        self._doc: Optional[dict] = None
        self._groups: List[Tuple[str, List[model.ElementInfo]]] = []
        self._info_by_id: Dict[ElementId, model.ElementInfo] = {}
        self._result: Optional[renderer.RenderResult] = None
        self._overlaps: List[model.Overlap] = []
        self._selected: Optional[ElementId] = None
        self._dirty = False
        self._needs_fit = False
        self._last_render_ms = 0.0
        self._syncing = False  # guards tree<->canvas selection loops
        # PIL frame buffer refs: QImage wraps this memory without owning it, so
        # both must outlive the QImage or Qt reads freed memory (hard crash).
        self._frame_bytes: Optional[bytes] = None
        self._frame_qimage: Optional[QImage] = None
        # Drag bookkeeping
        self._drag_origin: Optional[Tuple[int, int]] = None

        self._build_ui()
        self._build_shortcuts()

        self._timer = QTimer(self)
        self._timer.setInterval(AUTO_REFRESH_MS)
        self._timer.timeout.connect(self._rerender)

        self._populate_theme_list(initial_theme)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # ---- toolbar
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        self._theme_label = QLabel("")
        self._theme_label.setObjectName("toolbarTheme")
        toolbar.addWidget(self._theme_label)
        toolbar.addSeparator()

        self._act_refresh = toolbar.addAction("Refresh", self._rerender)
        self._act_refresh.setToolTip("Re-render the preview now (F5)")
        self._act_auto = toolbar.addAction("Live preview")
        self._act_auto.setCheckable(True)
        self._act_auto.setChecked(True)
        self._act_auto.setToolTip("Re-render every second so values move")
        self._act_auto.toggled.connect(self._on_auto_toggled)
        self._act_revert = toolbar.addAction("Revert", self._revert)
        self._act_revert.setToolTip("Reload the theme from disk, discarding in-memory moves")
        self._act_revert.setEnabled(False)
        toolbar.addSeparator()

        toolbar.addAction("Zoom -", self._zoom_out_act).setToolTip("Zoom out (Ctrl+-)")
        toolbar.addAction("Zoom +", self._zoom_in_act).setToolTip("Zoom in (Ctrl+=)")
        toolbar.addAction("Fit", self._fit_act).setToolTip("Fit panel to window (Ctrl+0)")
        toolbar.addAction("1:1", self._zoom_100_act).setToolTip("Zoom to 100% (Ctrl+1)")
        self._zoom_label = QLabel("100%")
        self._zoom_label.setObjectName("zoomLabel")
        toolbar.addWidget(self._zoom_label)

        # ---- left panel: themes + element tree
        left = QFrame()
        left.setObjectName("sidePanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 8, 10, 10)
        left_layout.setSpacing(6)

        themes_title = QLabel("THEMES")
        themes_title.setObjectName("panelTitle")
        left_layout.addWidget(themes_title)
        self._theme_list = QListWidget()
        self._theme_list.currentItemChanged.connect(self._on_theme_picked)
        left_layout.addWidget(self._theme_list, 1)

        elements_title = QLabel("ELEMENTS")
        elements_title.setObjectName("panelTitle")
        left_layout.addWidget(elements_title)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection)
        left_layout.addWidget(self._tree, 2)

        # ---- centre: canvas over problem bar
        self.canvas = DesignerCanvas()
        self.canvas.elementClicked.connect(self._on_canvas_clicked)
        self.canvas.elementHovered.connect(self._on_canvas_hovered)
        self.canvas.moveStarted.connect(self._on_move_started)
        self.canvas.moveDelta.connect(self._on_move_delta)
        self.canvas.moveFinished.connect(self._on_move_finished)
        self.canvas.zoomChanged.connect(
            lambda z: self._zoom_label.setText("%d%%" % round(z * 100)))

        problems = QFrame()
        problems.setObjectName("problemPanel")
        problems_layout = QVBoxLayout(problems)
        problems_layout.setContentsMargins(10, 8, 10, 10)
        problems_layout.setSpacing(6)
        self._problems_title = QLabel("PROBLEMS")
        self._problems_title.setObjectName("panelTitle")
        problems_layout.addWidget(self._problems_title)
        self._problem_list = QListWidget()
        self._problem_list.setObjectName("problemList")
        self._problem_list.itemClicked.connect(self._on_problem_clicked)
        problems_layout.addWidget(self._problem_list, 1)

        centre = QSplitter(Qt.Orientation.Vertical)
        centre.addWidget(self.canvas)
        centre.addWidget(problems)
        centre.setStretchFactor(0, 4)
        centre.setStretchFactor(1, 1)
        centre.setSizes([650, 160])

        # ---- right: inspector
        self.inspector = Inspector()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(centre)
        splitter.addWidget(self.inspector)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([260, 840, 340])

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(splitter)
        self.setCentralWidget(container)

        # ---- status bar
        status = QStatusBar()
        self.setStatusBar(status)
        self._chip_render = self._add_chip(status, "statusChip")
        self._chip_elements = self._add_chip(status, "statusChip")
        self._chip_problems = self._add_chip(status, "statusChip")
        self._chip_state = self._add_chip(status, "statusChip")
        self._hover_label = QLabel("")
        self._hover_label.setObjectName("statusChip")
        status.addWidget(self._hover_label, 1)

    @staticmethod
    def _add_chip(status: QStatusBar, object_name: str) -> QLabel:
        chip = QLabel("")
        chip.setObjectName(object_name)
        status.addPermanentWidget(chip)
        return chip

    def _build_shortcuts(self) -> None:
        def app_shortcut(sequence, handler):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)

        app_shortcut("F5", self._rerender)
        app_shortcut("Ctrl+=", self._zoom_in_act)
        app_shortcut("Ctrl++", self._zoom_in_act)
        app_shortcut("Ctrl+-", self._zoom_out_act)
        app_shortcut("Ctrl+0", self._fit_act)
        app_shortcut("Ctrl+1", self._zoom_100_act)
        app_shortcut("Escape", lambda: self._select_element(None))

        # Arrow-key nudges only while the canvas has focus, so the tree and
        # lists keep their normal arrow navigation.
        def canvas_shortcut(sequence, dx, dy):
            shortcut = QShortcut(QKeySequence(sequence), self.canvas)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda: self._nudge(dx, dy))

        canvas_shortcut("Left", -1, 0)
        canvas_shortcut("Right", 1, 0)
        canvas_shortcut("Up", 0, -1)
        canvas_shortcut("Down", 0, 1)
        canvas_shortcut("Shift+Left", -10, 0)
        canvas_shortcut("Shift+Right", 10, 0)
        canvas_shortcut("Shift+Up", 0, -10)
        canvas_shortcut("Shift+Down", 0, 10)

    # ---------------------------------------------------------- zoom acts

    def _zoom_in_act(self) -> None:
        self.canvas.zoom_in()

    def _zoom_out_act(self) -> None:
        self.canvas.zoom_out()

    def _fit_act(self) -> None:
        self.canvas.fit_to_window()

    def _zoom_100_act(self) -> None:
        self.canvas.zoom_100()

    # -------------------------------------------------------- theme loading

    def _populate_theme_list(self, initial: Optional[str]) -> None:
        names = model.list_themes()
        self._theme_list.blockSignals(True)
        self._theme_list.clear()
        current_row = 0
        for row, name in enumerate(names):
            self._theme_list.addItem(QListWidgetItem(name))
            if name == initial:
                current_row = row
        self._theme_list.blockSignals(False)
        if names:
            self._theme_list.setCurrentRow(current_row)  # triggers load

    def _on_theme_picked(self, current: Optional[QListWidgetItem], _prev) -> None:
        if current is not None:
            self._load_theme(current.text())

    def _load_theme(self, name: str, fit: bool = True) -> None:
        self._timer.stop()
        self._theme_name = name
        self._theme_dir = model.themes_dir() / name
        self._dirty = False
        self._act_revert.setEnabled(False)
        self._selected = None
        # Reverting keeps the user's zoom/pan; switching themes refits.
        self._needs_fit = fit
        self._theme_label.setText(name)
        try:
            self._doc = renderer.load_theme_document(self._theme_dir)
        except Exception as exc:
            self._doc = None
            self._groups = []
            self._info_by_id = {}
            self._tree.clear()
            self._show_fatal("theme.yaml could not be parsed: %s" % exc)
            self._update_title()
            return
        self._groups = model.enumerate_elements(self._doc)
        self._info_by_id = model.flatten(self._groups)
        self._populate_tree()
        self.inspector.set_element(None, None)
        self.canvas.set_selected(None)
        self._update_title()
        self._rerender()
        if self._act_auto.isChecked():
            self._timer.start()

    def _revert(self) -> None:
        if self._theme_name:
            selected = self._selected
            self._load_theme(self._theme_name, fit=False)
            if selected in self._info_by_id:
                self._select_element(selected)

    def _populate_tree(self) -> None:
        self._syncing = True
        self._tree.clear()
        for group_label, elements in self._groups:
            group_item = QTreeWidgetItem([group_label])
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            group_item.setForeground(0, QColor(style.TEXT_DIM))
            self._tree.addTopLevelItem(group_item)
            for info in elements:
                label = info.label + ("  (hidden)" if not info.shown else "")
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.ItemDataRole.UserRole, info.element_id)
                child.setToolTip(0, "%s\n%s" % (model.id_text(info.element_id), info.kind))
                if not info.shown:
                    child.setForeground(0, QColor(style.TEXT_FAINT))
                group_item.addChild(child)
            group_item.setExpanded(True)
        self._syncing = False

    # ------------------------------------------------------------ rendering

    def _rerender(self) -> None:
        if self._doc is None or self._theme_name is None:
            return
        started = time.perf_counter()
        try:
            result = renderer.render(self._theme_dir, document=self._doc)
        except renderer.ThemePreflightError as exc:
            self._show_fatal(str(exc))
            self._timer.stop()
            return
        except Exception as exc:
            self._show_fatal("render failed: %s: %s" % (type(exc).__name__, exc))
            return
        self._last_render_ms = (time.perf_counter() - started) * 1000.0
        self._result = result

        image = result.image
        width, height = image.size
        # QImage wraps the bytes without copying; keep both alive (see __init__).
        self._frame_bytes = image.tobytes("raw", "RGB")
        self._frame_qimage = QImage(self._frame_bytes, width, height,
                                    width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(self._frame_qimage)
        pixmap.setDevicePixelRatio(1.0)

        self._overlaps = model.compute_overlaps(
            result.bboxes, self._info_by_id, width, height)
        shapes = [(o.severity,
                   result.bboxes[o.a], result.bboxes[o.b], o.rect)
                  for o in self._overlaps]
        self.canvas.set_frame(pixmap, result.bboxes, shapes)

        self._refresh_problem_list()
        self._refresh_status()

        if self._needs_fit:
            self._needs_fit = False
            QTimer.singleShot(0, self.canvas.fit_to_window)

        # Keep the inspector's bbox line current for the selected element.
        if self._selected is not None:
            info = self._info_by_id.get(self._selected)
            if info is not None:
                self.inspector.set_element(info, result.bboxes.get(self._selected))

    def _show_fatal(self, message: str) -> None:
        self._problem_list.clear()
        item = QListWidgetItem("[error]  %s" % message)
        item.setForeground(QColor(style.SEV_ERROR))
        self._problem_list.addItem(item)
        self._problems_title.setText("PROBLEMS  (1)")

    # --------------------------------------------------------- problem bar

    def _refresh_problem_list(self) -> None:
        self._problem_list.clear()
        errors = self._result.errors if self._result else []

        for error in errors:
            where = model.id_text(error.element) if error.element else "theme"
            item = QListWidgetItem("[render error]  %s  -  %s" % (where, error.message))
            item.setForeground(QColor(_SEVERITY_COLOR["error"]))
            item.setToolTip("This element failed to draw in the preview:\n%s" % error.message)
            if error.element:
                item.setData(Qt.ItemDataRole.UserRole, tuple(error.element))
            self._problem_list.addItem(item)

        for overlap in self._overlaps:
            item = QListWidgetItem("[%s]  %s" % (
                _SEVERITY_LABEL[overlap.severity].lower(), overlap.message))
            item.setForeground(QColor(_SEVERITY_COLOR[overlap.severity]))
            item.setToolTip(overlap.detail)
            item.setData(Qt.ItemDataRole.UserRole, overlap.a)
            self._problem_list.addItem(item)

        total = len(errors) + len(self._overlaps)
        if total == 0:
            ok = QListWidgetItem("No problems: nothing overlaps and every element rendered.")
            ok.setForeground(QColor("#5fd08a"))
            ok.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._problem_list.addItem(ok)
            self._problems_title.setText("PROBLEMS")
        else:
            self._problems_title.setText("PROBLEMS  (%d)" % total)

    def _on_problem_clicked(self, item: QListWidgetItem) -> None:
        element_id = item.data(Qt.ItemDataRole.UserRole)
        if element_id:
            self._select_element(tuple(element_id))

    # ------------------------------------------------------------ status bar

    def _refresh_status(self) -> None:
        self._chip_render.setText("render %.1f ms" % self._last_render_ms)
        drawn = len(self._result.bboxes) if self._result else 0
        self._chip_elements.setText("%d elements drawn" % drawn)
        conflicts = sum(1 for o in self._overlaps if o.severity == "conflict")
        warnings = len(self._overlaps) - conflicts
        errors = len(self._result.errors) if self._result else 0
        if conflicts or warnings or errors:
            parts = []
            if conflicts:
                parts.append("%d conflict%s" % (conflicts, "s" if conflicts != 1 else ""))
            if warnings:
                parts.append("%d overlap%s" % (warnings, "s" if warnings != 1 else ""))
            if errors:
                parts.append("%d render error%s" % (errors, "s" if errors != 1 else ""))
            self._chip_problems.setText(", ".join(parts))
            self._chip_problems.setObjectName("statusChipWarn")
        else:
            self._chip_problems.setText("no problems")
            self._chip_problems.setObjectName("statusChipOk")
        # Re-polish so the objectName-based colour change takes effect.
        self._chip_problems.style().unpolish(self._chip_problems)
        self._chip_problems.style().polish(self._chip_problems)
        self._chip_state.setText(
            "modified (in memory only)" if self._dirty else "unmodified")

    def _update_title(self) -> None:
        name = self._theme_name or "no theme"
        marker = " *" if self._dirty else ""
        self.setWindowTitle("Theme Designer - %s%s" % (name, marker))

    # ------------------------------------------------------------ selection

    def _select_element(self, element_id: Optional[ElementId],
                        from_tree: bool = False) -> None:
        if self._syncing:
            return
        self._selected = element_id
        self.canvas.set_selected(element_id)

        info = self._info_by_id.get(element_id) if element_id else None
        bbox = None
        if info is not None and self._result is not None:
            bbox = self._result.bboxes.get(element_id)
        self.inspector.set_element(info, bbox)

        if not from_tree:
            self._syncing = True
            try:
                self._tree.clearSelection()
                if element_id is not None:
                    item = self._find_tree_item(element_id)
                    if item is not None:
                        item.setSelected(True)
                        self._tree.scrollToItem(item)
            finally:
                self._syncing = False

    def _find_tree_item(self, element_id: ElementId) -> Optional[QTreeWidgetItem]:
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == element_id:
                    return child
        return None

    def _on_tree_selection(self) -> None:
        if self._syncing:
            return
        items = self._tree.selectedItems()
        element_id = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        if element_id is not None:
            self._select_element(tuple(element_id), from_tree=True)

    def _on_canvas_clicked(self, element_id) -> None:
        self._select_element(tuple(element_id) if element_id else None)
        self.canvas.setFocus()

    def _on_canvas_hovered(self, element_id) -> None:
        if element_id:
            info = self._info_by_id.get(tuple(element_id))
            text = model.id_text(tuple(element_id))
            if info is not None and info.interval > 0:
                text += "   (refreshes every %gs)" % info.interval
            self._hover_label.setText(text)
        else:
            self._hover_label.setText("")

    # ---------------------------------------------------- move / nudge path

    def _element_position(self, element_id: ElementId) -> Optional[Tuple[int, int]]:
        info = self._info_by_id.get(element_id)
        if info is None:
            return None
        try:
            return int(info.node.get("X", 0)), int(info.node.get("Y", 0))
        except (TypeError, ValueError):
            return None

    def _set_element_position(self, element_id: ElementId, x: int, y: int) -> None:
        info = self._info_by_id.get(element_id)
        if info is None:
            return
        width, height = (self._result.image.size if self._result else (480, 320))
        info.node["X"] = max(0, min(int(x), width - 1))
        info.node["Y"] = max(0, min(int(y), height - 1))
        if not self._dirty:
            self._dirty = True
            self._act_revert.setEnabled(True)
            self._update_title()
        self._rerender()

    def _nudge(self, dx: int, dy: int) -> None:
        if self._selected is None:
            return
        position = self._element_position(self._selected)
        if position is None:
            return
        self._set_element_position(self._selected, position[0] + dx, position[1] + dy)

    def _on_move_started(self, element_id) -> None:
        self._drag_origin = self._element_position(tuple(element_id))
        self._timer.stop()  # the drag itself re-renders continuously

    def _on_move_delta(self, element_id, dx: int, dy: int) -> None:
        if self._drag_origin is None:
            return
        self._set_element_position(tuple(element_id),
                                   self._drag_origin[0] + dx,
                                   self._drag_origin[1] + dy)

    def _on_move_finished(self, _element_id) -> None:
        self._drag_origin = None
        if self._act_auto.isChecked():
            self._timer.start()

    # ------------------------------------------------------------- controls

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            self._timer.start()
        else:
            self._timer.stop()
