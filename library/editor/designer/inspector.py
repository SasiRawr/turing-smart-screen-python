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

"""Property inspector. Phase 1 is read-only, but each row keeps a reference to
its (node, key) pair in Qt.UserRole, so making it editable later means adding
an item delegate -- not rebuilding the panel."""

from typing import Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QFrame, QLabel, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from library.editor.designer import model, style

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
    """Theme colours come as '61, 184, 225' strings or [r, g, b] lists."""
    parts = None
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    if not parts or len(parts) not in (3, 4):
        return None
    try:
        channels = [int(float(p)) for p in parts]
    except (TypeError, ValueError):
        return None
    if not all(0 <= c <= 255 for c in channels):
        return None
    return QColor(*channels)


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
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None:
        return "-"
    return str(value)


class Inspector(QFrame):
    """Right-hand panel: element identity header + grouped property tree."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inspectorPanel")

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
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.header().setStretchLastSection(True)
        self._tree.setColumnWidth(0, 150)
        layout.addWidget(self._tree, 1)

        self._empty = QLabel("Select an element on the canvas\nor in the element tree.")
        self._empty.setObjectName("emptyHint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty, 1)

        self.set_element(None, None)

    def set_element(self, info: Optional[model.ElementInfo],
                    bbox: Optional[BBox]) -> None:
        """Show one element, or the empty state. Values are re-read from the
        live document node, so calling this again after a nudge refreshes."""
        self._tree.clear()
        has_element = info is not None
        self._tree.setVisible(has_element)
        self._empty.setVisible(not has_element)
        self._name.setVisible(has_element)
        self._path.setVisible(has_element)
        self._meta.setVisible(has_element)
        if not has_element:
            return

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

        node = info.node
        remaining = [k for k in node.keys() if not isinstance(node.get(k), dict)]

        def add_group(label: str, keys) -> None:
            present = [k for k in keys if k in remaining]
            if not present:
                return
            group_item = QTreeWidgetItem([label, ""])
            group_item.setFirstColumnSpanned(True)
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            group_item.setForeground(0, QColor(style.TEXT_DIM))
            self._tree.addTopLevelItem(group_item)
            for key in present:
                remaining.remove(key)
                value = node.get(key)
                row = QTreeWidgetItem([key, _format_value(value)])
                row.setForeground(0, QColor(style.TEXT_DIM))
                # Kept for the future edit path: which dict/key this row shows.
                row.setData(0, Qt.ItemDataRole.UserRole, (node, key))
                if key.endswith("COLOR"):
                    color = _parse_color(value)
                    if color is not None:
                        row.setIcon(1, _color_icon(color))
                group_item.addChild(row)
            group_item.setExpanded(True)

        for label, keys in _GROUPS:
            add_group(label, keys)
        if remaining:
            add_group("Other", list(remaining))
