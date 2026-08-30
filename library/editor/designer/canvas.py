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

"""Preview canvas: QGraphicsView showing the rendered panel at 1 scene unit ==
1 panel pixel, with zoom/pan, hit-testing, hover highlight, a selection outline
with corner handles, and overlap outlines. All decoration is painted by a
single overlay item with cosmetic pens so it stays 1px-crisp at any zoom."""

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QStyleOptionGraphicsItem)

from library.editor.designer import style

ElementId = Tuple[str, ...]
BBox = Tuple[int, int, int, int]

MIN_ZOOM = 0.15
MAX_ZOOM = 16.0
ZOOM_STEP = 1.2

_SEVERITY_COLOR = {
    "conflict": QColor(style.SEV_CONFLICT),
    "warning": QColor(style.SEV_WARNING),
    "notice": QColor(style.SEV_NOTICE),
}


def _make_checker() -> QPixmap:
    """Subtle two-tone checkerboard tile for the area outside the panel."""
    tile = QPixmap(32, 32)
    tile.fill(QColor(style.CANVAS_CHECKER_A))
    painter = QPainter(tile)
    painter.fillRect(0, 0, 16, 16, QColor(style.CANVAS_CHECKER_B))
    painter.fillRect(16, 16, 16, 16, QColor(style.CANVAS_CHECKER_B))
    painter.end()
    return tile


class _OverlayItem(QGraphicsItem):
    """Draws hover, selection (outline + corner handles) and overlap outlines
    on top of the panel pixmap. One item so z-order and repaints stay simple."""

    def __init__(self, canvas: "DesignerCanvas"):
        super().__init__()
        self._canvas = canvas
        self._rect = QRectF(0, 0, 1, 1)
        self.setZValue(10)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_panel_rect(self, width: int, height: int) -> None:
        self.prepareGeometryChange()
        # A little margin so handles on the panel edge are not clipped.
        self._rect = QRectF(-8, -8, width + 16, height + 16)

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, option, widget=None) -> None:
        canvas = self._canvas
        lod = QStyleOptionGraphicsItem.levelOfDetailFromTransform(
            painter.worldTransform())
        lod = max(lod, 1e-6)

        # Overlap regions: fill the intersection, dash-outline the two boxes.
        for severity, box_a, box_b, rect in canvas.overlap_shapes():
            color = _SEVERITY_COLOR.get(severity, _SEVERITY_COLOR["warning"])
            fill = QColor(color)
            fill.setAlpha(60)
            painter.fillRect(QRectF(rect[0], rect[1],
                                    rect[2] - rect[0], rect[3] - rect[1]), fill)
            pen = QPen(color, 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for box in (box_a, box_b):
                painter.drawRect(QRectF(box[0], box[1],
                                        box[2] - box[0], box[3] - box[1]))

        # Hover highlight (skipped when it is also the selection).
        hover = canvas.hover_bbox()
        if hover is not None and hover != canvas.selection_bbox():
            pen = QPen(QColor(style.HOVER_OUTLINE), 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(hover[0], hover[1],
                                    hover[2] - hover[0], hover[3] - hover[1]))

        # Selection: accent outline + eight square handles, constant screen size.
        sel = canvas.selection_bbox()
        if sel is not None:
            accent = QColor(style.ACCENT)
            rect = QRectF(sel[0], sel[1], sel[2] - sel[0], sel[3] - sel[1])
            pen = QPen(accent, 1.4)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            half = 3.5 / lod  # ~7px handles on screen regardless of zoom
            cx, cy = rect.center().x(), rect.center().y()
            points = [
                (rect.left(), rect.top()), (cx, rect.top()), (rect.right(), rect.top()),
                (rect.left(), cy), (rect.right(), cy),
                (rect.left(), rect.bottom()), (cx, rect.bottom()), (rect.right(), rect.bottom()),
            ]
            handle_pen = QPen(QColor("#0d1013"), 1)
            handle_pen.setCosmetic(True)
            painter.setPen(handle_pen)
            painter.setBrush(QBrush(accent))
            for px, py in points:
                painter.drawRect(QRectF(px - half, py - half, half * 2, half * 2))


class DesignerCanvas(QGraphicsView):
    """The preview canvas. Emits selection/hover/move intents; the window owns
    the document and re-renders."""

    elementClicked = Signal(object)           # ElementId | None
    elementHovered = Signal(object)           # ElementId | None
    moveStarted = Signal(object)              # ElementId
    moveDelta = Signal(object, int, int)      # ElementId, total dx, dy since start
    moveFinished = Signal(object)             # ElementId
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setBackgroundBrush(QBrush(_make_checker()))

        self._pixmap_item = QGraphicsPixmapItem()
        self._pixmap_item.setZValue(0)
        # Pixel-crisp at zoom >= 100%; smoothing is toggled per zoom level.
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self._scene.addItem(self._pixmap_item)

        self._overlay = _OverlayItem(self)
        self._scene.addItem(self._overlay)

        self._dims_label = QGraphicsSimpleTextItem()
        self._dims_label.setBrush(QBrush(QColor(style.TEXT_FAINT)))
        self._dims_label.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._dims_label.setZValue(5)
        self._scene.addItem(self._dims_label)

        self._panel_size: Tuple[int, int] = (0, 0)
        self._bboxes: Dict[ElementId, BBox] = {}
        self._overlaps: List[Tuple[str, BBox, BBox, BBox]] = []
        self._selected: Optional[ElementId] = None
        self._hovered: Optional[ElementId] = None
        self._zoom = 1.0

        # Drag state
        self._drag_id: Optional[ElementId] = None
        self._drag_start: Optional[QPointF] = None
        self._drag_active = False
        self._panning = False
        self._pan_last = None

    # ---- data in -------------------------------------------------------

    def set_frame(self, pixmap: QPixmap, bboxes: Dict[ElementId, BBox],
                  overlap_shapes: List[Tuple[str, BBox, BBox, BBox]]) -> None:
        """New rendered frame. overlap_shapes: (severity, box_a, box_b, rect)."""
        size_changed = (pixmap.width(), pixmap.height()) != self._panel_size
        self._panel_size = (pixmap.width(), pixmap.height())
        self._pixmap_item.setPixmap(pixmap)
        self._bboxes = bboxes
        self._overlaps = overlap_shapes
        if size_changed:
            w, h = self._panel_size
            self._overlay.set_panel_rect(w, h)
            self._dims_label.setText("%d x %d px" % (w, h))
            self._dims_label.setPos(0, h + 8)
            margin = 60
            self._scene.setSceneRect(-margin, -margin, w + margin * 2, h + margin * 2)
        self._overlay.update()

    def set_selected(self, element_id: Optional[ElementId]) -> None:
        self._selected = element_id
        self._overlay.update()

    # ---- overlay queries ----------------------------------------------

    def selection_bbox(self) -> Optional[BBox]:
        return self._bboxes.get(self._selected) if self._selected else None

    def hover_bbox(self) -> Optional[BBox]:
        return self._bboxes.get(self._hovered) if self._hovered else None

    def overlap_shapes(self) -> List[Tuple[str, BBox, BBox, BBox]]:
        return self._overlaps

    # ---- zoom ----------------------------------------------------------

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, factor: float) -> None:
        factor = max(MIN_ZOOM, min(MAX_ZOOM, factor))
        if abs(factor - self._zoom) < 1e-9:
            return
        self.scale(factor / self._zoom, factor / self._zoom)
        self._zoom = factor
        self._apply_scaling_mode()
        self.zoomChanged.emit(self._zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / ZOOM_STEP)

    def zoom_100(self) -> None:
        self.set_zoom(1.0)

    def fit_to_window(self) -> None:
        w, h = self._panel_size
        if w <= 0 or h <= 0:
            return
        self.fitInView(QRectF(-10, -10, w + 20, h + 30),
                       Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()
        self._apply_scaling_mode()
        self.zoomChanged.emit(self._zoom)

    def _apply_scaling_mode(self) -> None:
        # Smooth when zoomed out (downscale), nearest when zoomed in so panel
        # pixels stay square instead of blurring.
        smooth = self._zoom < 1.0
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        self._pixmap_item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation if smooth
            else Qt.TransformationMode.FastTransformation)

    # ---- hit testing ---------------------------------------------------

    def _hit_test(self, scene_pos: QPointF) -> Optional[ElementId]:
        """Smallest bounding box containing the point wins, so clicking a value
        on top of the background selects the value, not the background."""
        x, y = scene_pos.x(), scene_pos.y()
        best = None
        best_area = None
        for element_id, (x0, y0, x1, y1) in self._bboxes.items():
            if x0 <= x < x1 and y0 <= y < y1:
                area = (x1 - x0) * (y1 - y0)
                if best_area is None or area < best_area:
                    best, best_area = element_id, area
        return best

    # ---- events --------------------------------------------------------

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.set_zoom(self._zoom * (ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP))
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            hit = self._hit_test(scene_pos)
            self.elementClicked.emit(hit)
            if hit is not None:
                self._drag_id = hit
                self._drag_start = scene_pos
                self._drag_active = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_last is not None:
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())

        if self._drag_id is not None and event.buttons() & Qt.MouseButton.LeftButton:
            dx = scene_pos.x() - self._drag_start.x()
            dy = scene_pos.y() - self._drag_start.y()
            if self._drag_active or abs(dx) >= 2 or abs(dy) >= 2:
                if not self._drag_active:
                    self._drag_active = True
                    self.moveStarted.emit(self._drag_id)
                self.moveDelta.emit(self._drag_id, int(round(dx)), int(round(dy)))
            event.accept()
            return

        hover = self._hit_test(scene_pos)
        if hover != self._hovered:
            self._hovered = hover
            self._overlay.update()
            self.elementHovered.emit(hover)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_id is not None:
            if self._drag_active:
                self.moveFinished.emit(self._drag_id)
            self._drag_id = None
            self._drag_start = None
            self._drag_active = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hovered is not None:
            self._hovered = None
            self._overlay.update()
            self.elementHovered.emit(None)
        super().leaveEvent(event)
