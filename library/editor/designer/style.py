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

"""Dark theme for the designer. One place for every colour so the canvas
overlay, the problem bar and the stylesheet stay consistent."""

# Core palette -- the cyan accent matches the LoopTelemetry theme's bar colour.
ACCENT = "#3db8e1"
ACCENT_DIM = "#2a7e9b"
BG_WINDOW = "#181b20"
BG_PANEL = "#1e2228"
BG_INSET = "#15181d"
BG_HOVER = "#262b33"
BG_SELECTED = "#2a3a45"
BORDER = "#2c323b"
TEXT = "#d6dce3"
TEXT_DIM = "#8a93a0"
TEXT_FAINT = "#5c6570"

# Severity colours (shared by canvas outlines and problem-bar rows).
SEV_CONFLICT = "#ff5f56"
SEV_WARNING = "#ffab40"
SEV_NOTICE = "#e8d44d"
SEV_ERROR = "#ff5f56"

# Canvas
CANVAS_CHECKER_A = "#171a1f"
CANVAS_CHECKER_B = "#1b1f25"
CANVAS_PANEL_BORDER = "#454d58"
HOVER_OUTLINE = "#9fb4c4"

QSS = """
* {
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
}
QMainWindow, QWidget {
    background: %(BG_WINDOW)s;
    color: %(TEXT)s;
}
QToolTip {
    background: #10141a;
    color: %(TEXT)s;
    border: 1px solid %(BORDER)s;
    padding: 6px 8px;
}

/* ---- splitters ---- */
QSplitter::handle {
    background: %(BG_WINDOW)s;
}
QSplitter::handle:horizontal { width: 5px; }
QSplitter::handle:vertical { height: 5px; }
QSplitter::handle:hover { background: %(ACCENT_DIM)s; }

/* ---- side panels ---- */
QFrame#sidePanel, QFrame#inspectorPanel, QFrame#problemPanel {
    background: %(BG_PANEL)s;
    border: 1px solid %(BORDER)s;
    border-radius: 6px;
}
QLabel#panelTitle {
    color: %(TEXT_DIM)s;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.5px;
    padding: 2px 2px 0 2px;
}
QLabel#inspectorName {
    font-size: 15px;
    font-weight: 600;
    color: %(TEXT)s;
}
QLabel#inspectorPath {
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    color: %(ACCENT)s;
}
QLabel#inspectorMeta {
    color: %(TEXT_DIM)s;
    font-size: 11px;
}
QLabel#emptyHint {
    color: %(TEXT_FAINT)s;
    font-size: 12px;
}

/* ---- lists and trees ---- */
QListWidget, QTreeWidget {
    background: %(BG_INSET)s;
    border: 1px solid %(BORDER)s;
    border-radius: 4px;
    outline: none;
    padding: 2px;
}
QListWidget::item, QTreeWidget::item {
    padding: 4px 6px;
    border-radius: 3px;
}
QListWidget::item:hover, QTreeWidget::item:hover {
    background: %(BG_HOVER)s;
}
QListWidget::item:selected, QTreeWidget::item:selected {
    background: %(BG_SELECTED)s;
    color: #eaf6fc;
}
QTreeWidget::branch {
    background: transparent;
}
QHeaderView::section {
    background: %(BG_PANEL)s;
    color: %(TEXT_DIM)s;
    border: none;
    border-bottom: 1px solid %(BORDER)s;
    padding: 4px 6px;
    font-size: 11px;
    font-weight: 600;
}

/* ---- toolbar ---- */
QToolBar {
    background: %(BG_PANEL)s;
    border: none;
    border-bottom: 1px solid %(BORDER)s;
    padding: 4px 8px;
    spacing: 4px;
}
QToolBar::separator {
    background: %(BORDER)s;
    width: 1px;
    margin: 4px 6px;
}
QToolButton {
    background: transparent;
    color: %(TEXT)s;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 10px;
}
QToolButton:hover {
    background: %(BG_HOVER)s;
    border-color: %(BORDER)s;
}
QToolButton:pressed { background: %(BG_SELECTED)s; }
QToolButton:checked {
    background: %(BG_SELECTED)s;
    border-color: %(ACCENT_DIM)s;
    color: %(ACCENT)s;
}
QLabel#toolbarTheme {
    color: %(ACCENT)s;
    font-size: 13px;
    font-weight: 600;
    padding: 0 8px;
}
QLabel#zoomLabel {
    color: %(TEXT_DIM)s;
    padding: 0 6px;
    min-width: 44px;
}

/* ---- status bar ---- */
QStatusBar {
    background: %(BG_PANEL)s;
    border-top: 1px solid %(BORDER)s;
    color: %(TEXT_DIM)s;
}
QStatusBar::item { border: none; }
QLabel#statusChip {
    color: %(TEXT_DIM)s;
    padding: 1px 10px;
    border-right: 1px solid %(BORDER)s;
}
QLabel#statusChipWarn {
    color: %(SEV_WARNING)s;
    font-weight: 600;
    padding: 1px 10px;
    border-right: 1px solid %(BORDER)s;
}
QLabel#statusChipOk {
    color: #5fd08a;
    padding: 1px 10px;
    border-right: 1px solid %(BORDER)s;
}

/* ---- problem bar ---- */
QListWidget#problemList {
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
}

/* ---- scrollbars ---- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle {
    background: #333a44;
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover { background: #414a56; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QGraphicsView {
    border: 1px solid %(BORDER)s;
    border-radius: 6px;
}
""" % {
    "ACCENT": ACCENT, "ACCENT_DIM": ACCENT_DIM, "BG_WINDOW": BG_WINDOW,
    "BG_PANEL": BG_PANEL, "BG_INSET": BG_INSET, "BG_HOVER": BG_HOVER,
    "BG_SELECTED": BG_SELECTED, "BORDER": BORDER, "TEXT": TEXT,
    "TEXT_DIM": TEXT_DIM, "TEXT_FAINT": TEXT_FAINT, "SEV_WARNING": SEV_WARNING,
}


def apply(app) -> None:
    """Fusion base + dark stylesheet. Fusion keeps native quirks out of the QSS."""
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
