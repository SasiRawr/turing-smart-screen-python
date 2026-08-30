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

"""Property inspector. Each row shows one (node, key) leaf of the selected
element and is editable in place through PropertyDelegate; the delegate emits
the typed new value, the inspector re-emits it as propertyEdited, and the
window turns it into an undoable command.

The tree is rebuilt only when the selection moves to a DIFFERENT element.
Re-selecting the same element (which the render loop does every frame to keep
the bbox line current) refreshes row values in place -- rebuilding would
destroy any editor the user has open mid-edit."""

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QLabel,
                               QStyledItemDelegate, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from library.editor.designer import model, style
from library.editor.designer.delegate import PropertyDelegate, parse_color_string
from library.editor.designer.document import is_bool

BBox = Tuple[int, int, int, int]

# Display order. Only keys present on the element are shown; anything not
# listed lands in "Other" so unknown theme keys are never hidden.
_GROUPS = (
    ("Position & Size", ("X", "Y", "WIDTH", "HEIGHT", "RADIUS")),
    ("Text", ("TEXT", "ALIGN", "ANCHOR")),
    ("Font", ("FONT", "FONT_SIZE", "FONT_COLOR")),
    ("Fill & Background", ("BACKGROUND_COLOR", "BACKGROUND_IMAGE", "BAR_COLOR",
                           "BAR_OUTLINE", "LINE_COLOR", "LINE_WIDTH")),
    ("Axis", ("AXIS", "AXIS_COLOR", "AXIS_FONT", "AXIS_FONT_SIZE")),
    ("Range", ("MIN_VALUE", "MAX_VALUE", "AUTOSCALE")),
    ("Radial", ("ANGLE_START", "ANGLE_END", "ANGLE_STEPS", "ANGLE_SEP",
                "CLOCKWISE", "SHOW_TEXT", "SHOW_UNIT", "UNIT")),
    ("Visibility & Refresh", ("SHOW", "INTERVAL", "PATH")),
)


def _parse_color(value) -> Optional[QColor]:
    rgb = parse_color_string(value)
    return QColor(*rgb) if rgb is not None else None


def _color_icon(color: QColor) -> QIcon:
    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(style.BORDER))
    inner = QPixmap(12, 12)
    inner.fill(color)
    from PySide6.QtGui import QPainter
    painter = QPainter(pixmap)
    painter.drawPixmap(1, 1, inner)
    painter.end()
    return QIcon(pixmap)


def _format_value(value) -> str:
    if is_bool(value):
        return "True" if value else "False"
    if value is None:
        return "-"
    return str(value)


class _NoEditDelegate(QStyledItemDelegate):
    """Keeps the name column read-only while the row itself is editable."""

    def createEditor(self, _parent, _option, _index):
        return None


class Inspector(QFrame):
    """Right-hand panel: element identity header + grouped property tree."""

    # (node, key, new typed value) -- forwarded from the delegate.
    propertyEdited = Signal(object, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inspectorPanel")

        # (item, node, key) for every property row currently in the tree.
        self._rows: List[Tuple[QTreeWidgetItem, dict, str]] = []
        self._current: Optional[model.ElementInfo] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        title = QLabel("INSPECTOR")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        self._name = QLabel("")
        self._name.setObjectName("inspectorName")
        self._name.setWordWrap(True)
        layout.addWidget(self._name)

        self._path = QLabel("")
        self._path.setObjectName("inspectorPath")
        self._path.setWordWrap(True)
        self._path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._path)

        self._meta = QLabel("")
        self._meta.setObjectName("inspectorMeta")
        self._meta.setWordWrap(True)
        layout.addWidget(self._meta)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Property", "Value"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self._tree.header().setStretchLastSection(True)
        self._tree.setColumnWidth(0, 150)

        self._delegate = PropertyDelegate(self._tree)
        self._delegate.valueEdited.connect(self.propertyEdited)
        self._tree.setItemDelegateForColumn(1, self._delegate)
        self._tree.setItemDelegateForColumn(0, _NoEditDelegate(self._tree))
        layout.addWidget(self._tree, 1)

        self._empty = QLabel("Select an element on the canvas\nor in the element tree.")
        self._empty.setObjectName("emptyHint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty, 1)

        self.set_element(None, None)

    # ------------------------------------------------------------- content

    def set_element(self, info: Optional[model.ElementInfo],
                    bbox: Optional[BBox]) -> None:
        """Show one element, or the empty state.

        Called again with the SAME element (the render loop does this every
        frame), only the header and row values refresh -- the tree, and any
        open editor in it, stays alive."""
        same_element = (
            info is not None and self._current is not None
            and info.element_id == self._current.element_id
            and info.node is self._current.node)

        if same_element:
            self._current = info
            self._set_header(info, bbox)
            self.refresh_values()
            return

        self._current = info
        self._rows = []
        self._tree.clear()
        has_element = info is not None
        self._tree.setVisible(has_element)
        self._empty.setVisible(not has_element)
        self._name.setVisible(has_element)
        self._path.setVisible(has_element)
        self._meta.setVisible(has_element)
        if not has_element:
            return

        self._set_header(info, bbox)

        node = info.node
        remaining = [k for k in node.keys() if not isinstance(node.get(k), dict)]

        def add_group(label: str, keys) -> None:
            present = [k for k in keys if k in remaining]
            if not present:
                return
            group_item = QTreeWidgetItem([label, ""])
            group_item.setFirstColumnSpanned(True)
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not editable
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            group_item.setForeground(0, QColor(style.TEXT_DIM))
            self._tree.addTopLevelItem(group_item)
            for key in present:
                remaining.remove(key)
                row = QTreeWidgetItem([key, ""])
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsEditable)
                row.setForeground(0, QColor(style.TEXT_DIM))
                # The delegate reads which dict/key this row edits from here.
                row.setData(0, Qt.ItemDataRole.UserRole, (node, key))
                group_item.addChild(row)
                self._rows.append((row, node, key))
            group_item.setExpanded(True)

        for label, keys in _GROUPS:
            add_group(label, keys)
        if remaining:
            add_group("Other", list(remaining))
        self.refresh_values()

    def refresh_values(self) -> None:
        """Re-read every row's value from the live document node."""
        for item, node, key in self._rows:
            value = node.get(key)
            item.setText(1, _format_value(value))
            if key.endswith("COLOR"):
                color = _parse_color(value)
                item.setIcon(1, _color_icon(color) if color is not None else QIcon())

    def _set_header(self, info: model.ElementInfo,
                    bbox: Optional[BBox]) -> None:
        self._name.setText(info.label)
        self._path.setText(model.id_text(info.element_id))
        meta_parts = [info.kind]
        if info.interval > 0:
            meta_parts.append("refreshes every %gs" % info.interval)
        else:
            meta_parts.append("drawn once at startup")
        if not info.shown:
            meta_parts.append("hidden (SHOW: False)")
        if bbox is not None:
            meta_parts.append("box %d,%d - %d,%d  (%d x %d px)" % (
                bbox[0], bbox[1], bbox[2], bbox[3],
                bbox[2] - bbox[0], bbox[3] - bbox[1]))
        elif info.shown:
            meta_parts.append("not drawn in last render")
        self._meta.setText("  |  ".join(meta_parts))
