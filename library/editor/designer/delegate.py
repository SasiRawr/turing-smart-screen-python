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

"""Per-property editors for the inspector tree.

One QStyledItemDelegate covers the value column; the widget it creates depends
on the property key (and, as a fallback, the current value's Python type):

    ints     -> QSpinBox with a per-key range
    bools    -> True/False combo
    colours  -> line edit + swatch button opening QColorDialog; the value
                stays the on-disk 'R, G, B' string form, never a list
    fonts    -> combo of the .ttf/.otf files under res/fonts/
    enums    -> combo (ALIGN, ANCHOR)
    text     -> plain line edit

The delegate never writes into the item model. It computes the typed new
value and emits valueEdited(node, key, value); the window turns that into a
SetProperty command so every edit is undoable and re-renders the preview.
"""

from typing import Any, List, Optional, Tuple

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDoubleSpinBox,
                               QHBoxLayout, QLineEdit, QSpinBox,
                               QStyledItemDelegate, QToolButton, QWidget)

from library.editor.designer import model
from library.editor.designer.document import is_bool

# ---- property schema ----------------------------------------------------

# Key -> (minimum, maximum). Panel coordinates can exceed the 480x320 SIMU
# panel: bundled themes go up to 1920x1080-class layouts.
INT_RANGES = {
    "X": (0, 4096),
    "Y": (0, 4096),
    "WIDTH": (0, 4096),
    "HEIGHT": (0, 4096),
    "RADIUS": (1, 2048),
    "FONT_SIZE": (1, 400),
    "AXIS_FONT_SIZE": (1, 400),
    "LINE_WIDTH": (1, 128),
    "MIN_VALUE": (-1000000, 1000000),
    "MAX_VALUE": (-1000000, 1000000),
    "ANGLE_START": (-360, 720),
    "ANGLE_END": (-360, 720),
    "ANGLE_STEPS": (1, 3600),
    "ANGLE_SEP": (0, 360),
    "BLOCKS": (1, 1024),
    "SPACING": (0, 256),
    "INTERVAL": (0, 3600),
}

BOOL_KEYS = frozenset((
    "SHOW", "AUTOSCALE", "AXIS", "CLOCKWISE", "BAR_OUTLINE",
    "SHOW_TEXT", "SHOW_UNIT", "DRAW_BAR_BACKGROUND",
))

ENUM_OPTIONS = {
    "ALIGN": ["left", "center", "right"],
    # PIL text anchors: horizontal l/m/r x vertical a(scender)/t(op)/m(iddle)/
    # s(baseline)/b(ottom)/d(escender). The common ones, current value is
    # appended if it is not listed.
    "ANCHOR": ["lt", "mt", "rt", "lm", "mm", "rm", "ls", "ms", "rs",
               "lb", "mb", "rb"],
}

FONT_KEYS = frozenset(("FONT", "AXIS_FONT"))

KIND_INT = "int"
KIND_FLOAT = "float"
KIND_BOOL = "bool"
KIND_COLOR = "color"
KIND_FONT = "font"
KIND_ENUM = "enum"
KIND_TEXT = "text"


def classify(key: str, value: Any) -> str:
    """Decide which editor a property gets."""
    if key in BOOL_KEYS or is_bool(value):
        return KIND_BOOL
    if key.endswith("_COLOR"):
        return KIND_COLOR
    if key in FONT_KEYS:
        return KIND_FONT
    if key in ENUM_OPTIONS:
        return KIND_ENUM
    if key in INT_RANGES:
        # INTERVAL and MIN/MAX_VALUE may legitimately be floats in a theme.
        return KIND_FLOAT if isinstance(value, float) else KIND_INT
    if isinstance(value, float):
        return KIND_FLOAT
    if isinstance(value, int):
        return KIND_INT
    return KIND_TEXT


def list_fonts() -> List[str]:
    """Font files under res/fonts/, as the relative paths themes store."""
    fonts_dir = model.repo_root() / "res" / "fonts"
    found = []
    if fonts_dir.is_dir():
        for path in sorted(fonts_dir.rglob("*")):
            if path.suffix.lower() in (".ttf", ".otf", ".ttc"):
                found.append(path.relative_to(fonts_dir).as_posix())
    return found


def parse_color_string(value: Any) -> Optional[Tuple[int, int, int]]:
    """'61, 184, 225' (or a 3/4-item list) -> (r, g, b), else None."""
    parts = None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    if not parts or len(parts) not in (3, 4):
        return None
    try:
        channels = [int(float(part)) for part in parts]
    except (TypeError, ValueError):
        return None
    if not all(0 <= channel <= 255 for channel in channels):
        return None
    return tuple(channels[:3])


# ---- colour editor widget ----------------------------------------------

class ColorEditor(QWidget):
    """Line edit holding the 'R, G, B' string + a swatch button that opens
    QColorDialog. The string form is authoritative: it is what gets saved."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("R, G, B")
        layout.addWidget(self._edit, 1)
        self._button = QToolButton(self)
        self._button.setText("...")
        self._button.setToolTip("Pick a colour")
        self._button.clicked.connect(self._pick)
        layout.addWidget(self._button)
        self.setFocusProxy(self._edit)
        self._edit.textChanged.connect(self._update_swatch)

    def _pick(self) -> None:
        rgb = parse_color_string(self._edit.text()) or (255, 255, 255)
        chosen = QColorDialog.getColor(QColor(*rgb), self, "Pick a colour")
        if chosen.isValid():
            self.set_color_text("%d, %d, %d" % (chosen.red(), chosen.green(),
                                                chosen.blue()))

    def _update_swatch(self, _text: str = "") -> None:
        rgb = parse_color_string(self._edit.text())
        if rgb is not None:
            self._button.setStyleSheet(
                "QToolButton { background-color: rgb(%d, %d, %d); }" % rgb)
        else:
            self._button.setStyleSheet("")

    def color_text(self) -> str:
        return self._edit.text().strip()

    def set_color_text(self, text: str) -> None:
        self._edit.setText(text)
        self._update_swatch()


# ---- the delegate -------------------------------------------------------

class PropertyDelegate(QStyledItemDelegate):
    """Editor factory for the inspector's value column."""

    # (node, key, new typed value)
    valueEdited = Signal(object, str, object)

    def _node_key(self, index) -> Optional[Tuple[dict, str]]:
        stored = index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole)
        if not stored:
            return None
        node, key = stored
        return node, key

    # -- creation ---------------------------------------------------------

    def createEditor(self, parent, option, index):
        if index.column() != 1:
            return None
        stored = self._node_key(index)
        if stored is None:
            return None
        node, key = stored
        kind = classify(key, node.get(key))

        if kind == KIND_INT:
            editor = QSpinBox(parent)
            low, high = INT_RANGES.get(key, (-1000000, 1000000))
            editor.setRange(low, high)
            editor.setAccelerated(True)
            return editor
        if kind == KIND_FLOAT:
            editor = QDoubleSpinBox(parent)
            low, high = INT_RANGES.get(key, (-1000000, 1000000))
            editor.setRange(low, high)
            editor.setDecimals(3)
            return editor
        if kind == KIND_BOOL:
            editor = QComboBox(parent)
            editor.addItem("False", False)
            editor.addItem("True", True)
            return editor
        if kind == KIND_COLOR:
            return ColorEditor(parent)
        if kind == KIND_FONT:
            editor = QComboBox(parent)
            editor.addItems(list_fonts())
            return editor
        if kind == KIND_ENUM:
            editor = QComboBox(parent)
            editor.addItems(ENUM_OPTIONS[key])
            return editor
        return QLineEdit(parent)

    # -- editor <- model --------------------------------------------------

    def setEditorData(self, editor, index) -> None:
        stored = self._node_key(index)
        if stored is None:
            return
        node, key = stored
        value = node.get(key)

        if isinstance(editor, QSpinBox):
            try:
                editor.setValue(int(value))
            except (TypeError, ValueError):
                editor.setValue(0)
        elif isinstance(editor, QDoubleSpinBox):
            try:
                editor.setValue(float(value))
            except (TypeError, ValueError):
                editor.setValue(0.0)
        elif isinstance(editor, QComboBox):
            if classify(key, value) == KIND_BOOL:
                editor.setCurrentIndex(1 if value else 0)
            else:
                text = "" if value is None else str(value)
                found = editor.findText(text)
                if found < 0 and text:
                    editor.addItem(text)  # keep unknown current values visible
                    found = editor.count() - 1
                with QSignalBlocker(editor):
                    editor.setCurrentIndex(max(found, 0))
        elif isinstance(editor, ColorEditor):
            rgb = parse_color_string(value)
            editor.set_color_text(
                "%d, %d, %d" % rgb if rgb else ("" if value is None else str(value)))
        elif isinstance(editor, QLineEdit):
            editor.setText("" if value is None else str(value))

    # -- editor -> command ------------------------------------------------

    def setModelData(self, editor, _model, index) -> None:
        """Emit the typed new value instead of writing the item: the window
        owns the document and routes this through an undoable command."""
        stored = self._node_key(index)
        if stored is None:
            return
        node, key = stored
        old = node.get(key)
        new = self._editor_value(editor, key, old)
        if new is None:
            return  # invalid input: keep the old value
        if _same_value(old, new):
            return
        self.valueEdited.emit(node, key, new)

    def _editor_value(self, editor, key: str, old) -> Optional[Any]:
        if isinstance(editor, QSpinBox):
            editor.interpretText()
            return editor.value()
        if isinstance(editor, QDoubleSpinBox):
            editor.interpretText()
            value = editor.value()
            # Don't silently turn an int property into a float.
            return int(value) if isinstance(old, int) and not is_bool(old) \
                and value == int(value) else value
        if isinstance(editor, QComboBox):
            if classify(key, old) == KIND_BOOL:
                return bool(editor.currentData())
            text = editor.currentText().strip()
            return text if text else None
        if isinstance(editor, ColorEditor):
            text = editor.color_text()
            rgb = parse_color_string(text)
            if rgb is None:
                return None
            return "%d, %d, %d" % rgb  # normalized on-disk string form
        if isinstance(editor, QLineEdit):
            return editor.text()
        return None


def _same_value(old, new) -> bool:
    if is_bool(old) or is_bool(new):
        return is_bool(old) == is_bool(new) and bool(old) == bool(new)
    return old == new
