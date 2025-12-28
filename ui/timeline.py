"""
Timeline Widget
Provides playback controls and timeline scrubbing with keyframe markers.
"""

from typing import List, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QLabel,
    QCheckBox,
    QScrollBar,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath


class KeyframeMarkerBar(QWidget):
    """Custom bar that renders keyframe markers above the timeline slider."""

    markerClicked = pyqtSignal(float)
    markerRemoveRequested = pyqtSignal(list)
    markerDragRequested = pyqtSignal(list, float)
    selectionChanged = pyqtSignal(list)
    zoomRequested = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._markers: List[float] = []
        self._duration: float = 0.0
        self._current_time: float = 0.0
        self._view_start: float = 0.0
        self._view_duration: float = 0.0
        self.setMinimumHeight(24)
        self.setMaximumHeight(28)
        self.setMouseTracking(True)
        self._drag_active = False
        self._drag_moved = False
        self._drag_reference_time = 0.0
        self._drag_preview_delta = 0.0
        self._drag_origin_times: List[float] = []
        self._selected_markers: List[float] = []
        self._box_selecting = False
        self._box_origin = QPointF()
        self._box_current = QPointF()

    def set_markers(self, markers: List[float], duration: float):
        self._markers = sorted(markers or [])
        self._duration = max(0.0, float(duration))
        if self._view_duration <= 0.0 or self._view_duration > self._duration:
            self._view_start = 0.0
            self._view_duration = max(self._duration, 1e-6)
        self._prune_selection()
        self.update()

    def set_view_window(self, start: float, duration: float):
        duration = max(1e-6, float(duration))
        if self._duration > 0.0:
            start = min(max(0.0, start), max(0.0, self._duration - duration))
        else:
            start = 0.0
        self._view_start = start
        self._view_duration = duration
        self.update()

    def set_current_time(self, time_value: float):
        self._current_time = max(0.0, float(time_value))
        self.update()

    def set_selected_markers(self, markers: List[float]):
        snapped = self._snap_markers(markers)
        self._update_selection(snapped, emit=False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, self.palette().window())

        baseline_y = rect.height() - 6
        pen = QPen(QColor(120, 120, 120))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(4, baseline_y, rect.width() - 4, baseline_y)

        if self._view_duration <= 0.0 or not self._markers:
            return

        highlight_color = self.palette().color(self.palette().ColorRole.Highlight)
        highlight_pen = QPen(highlight_color)
        highlight_pen.setWidth(2)
        marker_brush = QColor(180, 180, 180)
        highlight_brush = highlight_color
        tolerance = max(1e-6, self._view_duration * 1e-4)

        for marker in self._markers:
            if marker < self._view_start - tolerance or marker > self._view_start + self._view_duration + tolerance:
                continue
            render_time = marker
            if self._drag_active and self._is_drag_target(marker):
                render_time = self._clamp_time(marker + self._drag_preview_delta)
            x = self._time_to_x(render_time)
            selected = self._is_selected(marker)
            painter.setPen(highlight_pen if selected else pen)
            painter.setBrush(highlight_brush if selected else marker_brush)
            path = self._triangle_path(x, baseline_y - 1, 6)
            painter.drawPath(path)

        if self._box_selecting:
            painter.setPen(QPen(highlight_color, 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(highlight_color.red(), highlight_color.green(), highlight_color.blue(), 60))
            rect = self._selection_rect()
            if rect:
                painter.drawRect(rect)

    def mousePressEvent(self, event):
        if not self._markers or self._view_duration <= 0.0:
            return super().mousePressEvent(event)
        click_x = event.position().x()
        closest_time, closest_distance = self._locate_marker(click_x)
        modifiers = event.modifiers()
        if closest_time is not None and closest_distance is not None and closest_distance <= 8:
            if event.button() == Qt.MouseButton.RightButton:
                targets = (
                    list(self._selected_markers)
                    if self._is_selected(closest_time) and self._selected_markers
                    else [closest_time]
                )
                self.markerRemoveRequested.emit(targets)
                return
            if event.button() == Qt.MouseButton.LeftButton:
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    self._toggle_selection([closest_time])
                    return
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self._add_selection([closest_time])
                    return
                if not self._is_selected(closest_time):
                    self._replace_selection([closest_time])
                self._drag_active = True
                self._drag_moved = False
                self._drag_reference_time = closest_time
                self._drag_preview_delta = 0.0
                self._drag_origin_times = list(self._selected_markers) or [closest_time]
                self.update()
                return
        if event.button() == Qt.MouseButton.LeftButton:
            self._box_selecting = True
            self._box_origin = event.position()
            self._box_current = event.position()
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and self._view_duration > 0.0:
            new_time = self._clamp_time(self._x_to_time(event.position().x()))
            delta = new_time - self._drag_reference_time
            if abs(delta - self._drag_preview_delta) > max(1e-6, self._view_duration * 1e-5):
                self._drag_preview_delta = delta
                self._drag_moved = True
                self.update()
            return
        if self._box_selecting:
            self._box_current = event.position()
            self.update()
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_active and event.button() == Qt.MouseButton.LeftButton:
            delta = self._drag_preview_delta
            moved = self._drag_moved and abs(delta) > max(1e-6, self._view_duration * 1e-5)
            self._drag_active = False
            self._drag_moved = False
            origin_time = self._drag_reference_time
            origin_targets = list(self._drag_origin_times)
            self._drag_origin_times = []
            self._drag_preview_delta = 0.0
            self.update()
            if moved:
                self.markerDragRequested.emit(origin_targets or [origin_time], delta)
            else:
                self.markerClicked.emit(origin_time)
            return
        if self._box_selecting and event.button() == Qt.MouseButton.LeftButton:
            rect = self._selection_rect()
            self._box_selecting = False
            targets = []
            if rect:
                targets = self._markers_in_rect(rect)
            mode = self._selection_mode(event.modifiers())
            if targets or mode == "replace":
                self._apply_selection_change(targets, mode)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self._view_duration <= 0.0 or self._duration <= 0.0:
            return super().wheelEvent(event)
        delta = event.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(event)
        factor = 0.85 if delta > 0 else 1.15
        target_time = self._x_to_time(event.position().x())
        self.zoomRequested.emit(factor, target_time)
        event.accept()

    def _time_to_x(self, time_value: float) -> float:
        usable_width = max(1.0, self.width() - 8.0)
        if self._view_duration <= 0.0:
            return 4.0
        normalized = (time_value - self._view_start) / self._view_duration
        clamped = min(max(normalized, 0.0), 1.0)
        return 4.0 + clamped * usable_width

    def _x_to_time(self, x: float) -> float:
        usable_width = max(1.0, self.width() - 8.0)
        normalized = min(max((x - 4.0) / usable_width, 0.0), 1.0)
        return self._view_start + normalized * max(self._view_duration, 1e-6)

    def _locate_marker(self, click_x: float):
        closest_time = None
        closest_distance = None
        for marker in self._markers:
            if marker < self._view_start or marker > self._view_start + self._view_duration:
                continue
            distance = abs(click_x - self._time_to_x(marker))
            if closest_distance is None or distance < closest_distance:
                closest_distance = distance
                closest_time = marker
        return closest_time, closest_distance

    @staticmethod
    def _triangle_path(center_x: float, base_y: float, size: float):
        half = size / 2.0
        points = [
            QPointF(center_x - half, base_y),
            QPointF(center_x + half, base_y),
            QPointF(center_x, base_y - size),
        ]
        path = QPainterPath()
        path.moveTo(points[0])
        path.lineTo(points[1])
        path.lineTo(points[2])
        path.closeSubpath()
        return path

    def _clamp_time(self, value: float) -> float:
        return min(max(value, 0.0), max(self._duration, 0.0))

    def _time_epsilon(self) -> float:
        return max(1e-5, self._duration * 1e-4)

    def _is_selected(self, time_value: float) -> bool:
        eps = self._time_epsilon()
        return any(abs(time_value - selected) <= eps for selected in self._selected_markers)

    def _is_drag_target(self, time_value: float) -> bool:
        eps = self._time_epsilon()
        return any(abs(time_value - target) <= eps for target in self._drag_origin_times)

    def _selection_rect(self) -> Optional[QRectF]:
        if not self._box_selecting:
            return None
        x1 = self._box_origin.x()
        y1 = self._box_origin.y()
        x2 = self._box_current.x()
        y2 = self._box_current.y()
        if abs(x2 - x1) < 1 and abs(y2 - y1) < 1:
            return None
        left = min(x1, x2)
        right = max(x1, x2)
        top = min(y1, y2)
        bottom = max(y1, y2)
        return QRectF(left, top, right - left, bottom - top)

    def _markers_in_rect(self, rect: QRectF) -> List[float]:
        if rect.width() <= 0 or rect.height() <= 0:
            return []
        selected: List[float] = []
        for marker in self._markers:
            x = self._time_to_x(marker)
            if rect.left() <= x <= rect.right() and rect.top() <= self.height() and rect.bottom() >= 0:
                selected.append(marker)
        return selected

    def _selection_mode(self, modifiers: Qt.KeyboardModifier) -> str:
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return "toggle"
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return "add"
        return "replace"

    def _apply_selection_change(self, targets: List[float], mode: str):
        if mode == "replace":
            self._replace_selection(targets)
        elif mode == "add":
            self._add_selection(targets)
        else:
            self._toggle_selection(targets)

    def _replace_selection(self, times: List[float]):
        snapped = self._snap_markers(times)
        self._update_selection(snapped, emit=True)

    def _add_selection(self, times: List[float]):
        new_sel = list(self._selected_markers)
        for time in self._snap_markers(times):
            if not self._is_selected(time):
                new_sel.append(time)
        self._update_selection(new_sel, emit=True)

    def _toggle_selection(self, times: List[float]):
        eps = self._time_epsilon()
        current = list(self._selected_markers)
        for time in self._snap_markers(times):
            removed = False
            for idx, existing in enumerate(current):
                if abs(existing - time) <= eps:
                    current.pop(idx)
                    removed = True
                    break
            if not removed:
                current.append(time)
        self._update_selection(current, emit=True)

    def _update_selection(self, selection: List[float], emit: bool):
        unique: List[float] = []
        eps = self._time_epsilon()
        for value in sorted(selection):
            if any(abs(value - existing) <= eps for existing in unique):
                continue
            if self._snap_to_marker(value) is None:
                continue
            unique.append(self._snap_to_marker(value) or value)
        if len(unique) == len(self._selected_markers) and all(
            abs(a - b) <= eps for a, b in zip(unique, self._selected_markers)
        ):
            return
        self._selected_markers = unique
        if emit:
            self.selectionChanged.emit(list(self._selected_markers))
        self.update()

    def _snap_to_marker(self, target: float) -> Optional[float]:
        eps = self._time_epsilon()
        for marker in self._markers:
            if abs(marker - target) <= eps:
                return marker
        return None

    def _snap_markers(self, times: List[float]) -> List[float]:
        snapped: List[float] = []
        for time in times:
            snapped_time = self._snap_to_marker(time)
            if snapped_time is not None:
                snapped.append(snapped_time)
        return snapped

    def _prune_selection(self):
        if not self._selected_markers:
            return
        snapped = self._snap_markers(self._selected_markers)
        self._update_selection(snapped, emit=True)


class TimelineWidget(QWidget):
    """Timeline widget with playback controls."""

    play_toggled = pyqtSignal()
    loop_toggled = pyqtSignal(int)
    time_changed = pyqtSignal(int)
    keyframe_marker_clicked = pyqtSignal(float)
    keyframe_marker_remove_requested = pyqtSignal(list)
    keyframe_marker_dragged = pyqtSignal(list, float)
    keyframe_selection_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms: float = 0.0
        self._view_start_ms: float = 0.0
        self._view_duration_ms: float = 0.0
        self._current_time_ms: float = 0.0
        self._min_view_ms: float = 100.0
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.play_toggled.emit)
        controls.addWidget(self.play_btn)

        self.loop_checkbox = QCheckBox("Loop")
        self.loop_checkbox.setChecked(True)
        self.loop_checkbox.stateChanged.connect(self.loop_toggled.emit)
        controls.addWidget(self.loop_checkbox)

        self.time_label = QLabel("0.00 / 0.00s")
        controls.addWidget(self.time_label)
        controls.addStretch()

        layout.addLayout(controls)

        self.keyframe_bar = KeyframeMarkerBar()
        self.keyframe_bar.markerClicked.connect(self.keyframe_marker_clicked.emit)
        self.keyframe_bar.markerRemoveRequested.connect(self._on_marker_remove_requested)
        self.keyframe_bar.markerDragRequested.connect(self._on_marker_drag_requested)
        self.keyframe_bar.selectionChanged.connect(self.keyframe_selection_changed.emit)
        self.keyframe_bar.zoomRequested.connect(self._on_zoom_requested)
        layout.addWidget(self.keyframe_bar)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setMaximum(1)
        self.timeline_slider.valueChanged.connect(self._on_slider_value_changed)
        layout.addWidget(self.timeline_slider)

        self.timeline_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scrollbar.setVisible(False)
        self.timeline_scrollbar.valueChanged.connect(self._on_scrollbar_value_changed)
        layout.addWidget(self.timeline_scrollbar)

    def set_play_button_text(self, text: str):
        self.play_btn.setText(text)

    def set_time_label(self, text: str):
        self.time_label.setText(text)

    def set_slider_maximum(self, maximum: int):
        duration_seconds = max(0.0, maximum / 1000.0)
        self.set_timeline_duration(duration_seconds)

    def set_timeline_duration(self, duration_seconds: float):
        self._duration_ms = max(1.0, float(duration_seconds) * 1000.0)
        if self._view_duration_ms <= 0.0 or self._view_duration_ms > self._duration_ms:
            self._view_duration_ms = self._duration_ms
            self._view_start_ms = 0.0
        else:
            max_start = max(0.0, self._duration_ms - self._view_duration_ms)
            self._view_start_ms = min(max(0.0, self._view_start_ms), max_start)
        self._update_scrollbar()
        self._update_slider_range()
        self.keyframe_bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)

    def set_keyframe_markers(self, markers: List[float], duration: float):
        self.keyframe_bar.set_markers(markers, duration)
        self.keyframe_bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)

    def set_marker_selection(self, markers: List[float]):
        self.keyframe_bar.set_selected_markers(markers)

    def set_current_time(self, time_value: float):
        self._current_time_ms = max(0.0, min(time_value * 1000.0, self._duration_ms))
        self._ensure_time_visible(self._current_time_ms)
        self._update_slider_range()
        self.keyframe_bar.set_current_time(self._current_time_ms / 1000.0)

    def _on_slider_value_changed(self, value: int):
        actual_ms = self._view_start_ms + float(value)
        actual_ms = max(0.0, min(actual_ms, self._duration_ms))
        self._current_time_ms = actual_ms
        self._ensure_time_visible(actual_ms)
        self.keyframe_bar.set_current_time(actual_ms / 1000.0)
        self.time_changed.emit(int(actual_ms))

    def _on_scrollbar_value_changed(self, value: int):
        self._view_start_ms = float(value)
        self._update_slider_range()
        self.keyframe_bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)

    def _update_slider_range(self):
        view_ms = max(1.0, self._view_duration_ms)
        slider_value = int(min(max(self._current_time_ms - self._view_start_ms, 0.0), view_ms))
        self.timeline_slider.blockSignals(True)
        self.timeline_slider.setMaximum(int(view_ms))
        self.timeline_slider.setValue(slider_value)
        self.timeline_slider.blockSignals(False)

    def _update_scrollbar(self):
        if self._view_duration_ms >= self._duration_ms or self._duration_ms <= 0.0:
            self.timeline_scrollbar.hide()
            self.timeline_scrollbar.setRange(0, 0)
        else:
            max_start = int(max(0.0, self._duration_ms - self._view_duration_ms))
            self.timeline_scrollbar.blockSignals(True)
            self.timeline_scrollbar.setRange(0, max_start)
            self.timeline_scrollbar.setPageStep(int(self._view_duration_ms))
            self.timeline_scrollbar.setSingleStep(max(1, int(self._view_duration_ms * 0.1)))
            self.timeline_scrollbar.setValue(int(self._view_start_ms))
            self.timeline_scrollbar.blockSignals(False)
            self.timeline_scrollbar.show()

    def _ensure_time_visible(self, time_ms: float):
        if time_ms < self._view_start_ms:
            self._view_start_ms = time_ms - self._view_duration_ms * 0.1
            if self._view_start_ms < 0.0:
                self._view_start_ms = 0.0
            self._update_scrollbar()
            self.keyframe_bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)
        elif time_ms > self._view_start_ms + self._view_duration_ms:
            self._view_start_ms = time_ms - self._view_duration_ms * 0.9
            max_start = max(0.0, self._duration_ms - self._view_duration_ms)
            if self._view_start_ms > max_start:
                self._view_start_ms = max_start
            self._update_scrollbar()
            self.keyframe_bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)
        self._update_slider_range()

    def _on_zoom_requested(self, factor: float, anchor_time: float):
        if self._duration_ms <= 0.0:
            return
        anchor_ms = max(0.0, min(anchor_time * 1000.0, self._duration_ms))
        new_span = self._view_duration_ms * factor
        new_span = max(self._min_view_ms, min(new_span, self._duration_ms))
        if new_span >= self._duration_ms:
            self._view_start_ms = 0.0
        else:
            ratio = 0.0
            if self._view_duration_ms > 0:
                ratio = (anchor_ms - self._view_start_ms) / self._view_duration_ms
                ratio = min(max(ratio, 0.0), 1.0)
            new_start = anchor_ms - ratio * new_span
            max_start = max(0.0, self._duration_ms - new_span)
            self._view_start_ms = min(max(0.0, new_start), max_start)
        self._view_duration_ms = new_span
        self._update_scrollbar()
        self.keyframe_bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)
        self._update_slider_range()

    def _on_marker_remove_requested(self, targets: List[float]):
        if not targets:
            return
        self.keyframe_marker_remove_requested.emit(list(targets))

    def _on_marker_drag_requested(self, targets: List[float], delta: float):
        if not targets:
            return
        self.keyframe_marker_dragged.emit(list(targets), float(delta))
