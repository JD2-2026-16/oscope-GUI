from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.mock_scope import MockScope
from backend.scope_source import ScopeFrame, ScopeSource
from backend.usb_scope import UsbScope
from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)


@dataclass(slots=True)
class ScopeSettings:
    """
    GUI-side control state.

    Keeping all user controls in one dataclass makes it easier to reason about
    what affects rendering and what should be sent to firmware later.
    """

    ch1_enabled: bool = True
    ch2_enabled: bool = True
    ch1_v_div: float = 1.0
    ch2_v_div: float = 1.0
    s_div: float = 200e-6
    trigger_level_v: float = 0.0
    trigger_source: str = "CH1"


class WaveformWidget(QWidget):
    """
    Custom plot area.

    This widget only knows how to draw a frame + settings. It does not talk to
    USB, timers, or controls directly, which keeps responsibilities clear.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Plot area should expand with the window and stay readable at small sizes.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(360)
        # Last complete frame received from acquisition backend (USB or mock).
        self.frame_data: ScopeFrame | None = None
        # Snapshot of UI/front-panel settings used for rendering.
        self.settings = ScopeSettings()
        # Cache static background (solid fill + grid) to avoid redrawing it every frame.
        self._bg_cache: QPixmap | None = None
        self._bg_cache_size: tuple[int, int] | None = None
        # Latched status displayed in the overlay text.
        self._trigger_found: bool = False

    def set_frame(self, frame: ScopeFrame) -> None:
        """Push in latest frame and request repaint."""
        self.frame_data = frame
        self.update()

    def set_settings(self, settings: ScopeSettings) -> None:
        """Push updated knob/button values and repaint."""
        self.settings = settings
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        # Keep redraw latency low; anti-aliasing is expensive at high refresh rates.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        rect = self.rect()
        # Draw static layers first, then dynamic overlays/waveforms.
        self._draw_cached_background(painter, rect.width(), rect.height())

        # Draw trigger level even before data exists so control feedback is visible.
        self._draw_trigger_line(painter, rect.width(), rect.height())

        if self.frame_data is None or self.frame_data.sample_count < 2:
            painter.setPen(QColor("#7F8FA6"))
            painter.drawText(14, 20, "No frame yet. Click Connect, then Run.")
            return

        # Compute one shared visible sample window so CH1/CH2 stay time-aligned.
        visible_count = self._compute_visible_count(self.frame_data.sample_count)
        if self.frame_data.trigger_found is not None:
            self._trigger_found = bool(self.frame_data.trigger_found)
        else:
            self._trigger_found = False
        trigger_samples = (
            self.frame_data.ch2
            if self.settings.trigger_source == "CH2"
            else self.frame_data.ch1
        )
        shared_window_start = self._choose_window_start(
            trigger_samples, visible_count, self.settings.trigger_source
        )

        if self.settings.ch1_enabled:
            self._draw_channel(
                painter=painter,
                samples=self.frame_data.ch1,
                width=rect.width(),
                height=rect.height(),
                v_div=self.settings.ch1_v_div,
                color=QColor("#FFD84D"),
                source_name="CH1",
                forced_window_start=shared_window_start,
            )
        if self.settings.ch2_enabled:
            self._draw_channel(
                painter=painter,
                samples=self.frame_data.ch2,
                width=rect.width(),
                height=rect.height(),
                v_div=self.settings.ch2_v_div,
                color=QColor("#65B7FF"),
                source_name="CH2",
                forced_window_start=shared_window_start,
            )

        painter.setPen(QColor("#7F8FA6"))
        painter.drawText(
            14,
            20,
            (
                f"CH1 {self.settings.ch1_v_div:.2f} V/div   "
                f"CH2 {self.settings.ch2_v_div:.2f} V/div   "
                f"{self._format_time_div(self.settings.s_div)}   "
                f"Trig {self.settings.trigger_source} {self.settings.trigger_level_v:+.2f} V   "
                f"Trigger: {'FOUND' if self._trigger_found else 'NOT FOUND'}"
            ),
        )

    def _draw_grid(self, painter: QPainter, width: int, height: int) -> None:
        # Scope style grid: 10 horizontal divisions, 8 vertical divisions.
        grid_pen = QPen(QColor("#1D2633"))
        painter.setPen(grid_pen)

        x_step = max(width // 10, 1)
        y_step = max(height // 8, 1)

        for x in range(0, width, x_step):
            painter.drawLine(x, 0, x, height)
        for y in range(0, height, y_step):
            painter.drawLine(0, y, width, y)

    def _draw_cached_background(
        self, painter: QPainter, width: int, height: int
    ) -> None:
        # Rebuild cached pixmap only when the widget size changes.
        size = (width, height)
        if self._bg_cache is None or self._bg_cache_size != size:
            bg = QPixmap(width, height)
            bg.fill(QColor("#10131A"))
            bg_painter = QPainter(bg)
            self._draw_grid(bg_painter, width, height)
            bg_painter.end()
            self._bg_cache = bg
            self._bg_cache_size = size
        painter.drawPixmap(0, 0, self._bg_cache)

    def _draw_trigger_line(self, painter: QPainter, width: int, height: int) -> None:
        # Scale trigger line against the active trigger source channel.
        v_div = (
            self.settings.ch2_v_div
            if self.settings.trigger_source == "CH2"
            else self.settings.ch1_v_div
        )
        volts_full_scale = max(v_div * 4.0, 1e-6)  # +/-4 divisions around center.
        y = height / 2.0 - (self.settings.trigger_level_v / volts_full_scale) * (
            height / 2.0
        )

        trigger_color = (
            QColor("#65B7FF") if self.settings.trigger_source == "CH2" else QColor("#FFD84D")
        )
        trigger_pen = QPen(trigger_color)
        trigger_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(trigger_pen)
        painter.drawLine(0, int(y), width, int(y))

    def _draw_channel(
        self,
        painter: QPainter,
        samples: list[float],
        width: int,
        height: int,
        v_div: float,
        color: QColor,
        source_name: str,
        forced_window_start: int | None = None,
    ) -> None:
        # Convert current time/div setting into how many samples to display.
        visible_count = self._compute_visible_count(len(samples))

        # Trigger alignment: try to start window near a threshold crossing so each
        # repaint is stable and looks like a scope instead of random drift.
        if forced_window_start is None:
            window_start = self._choose_window_start(
                samples, visible_count, source_name
            )
        else:
            window_start = forced_window_start
        # Slice the frame down to exactly what is visible on the screen.
        window = samples[window_start : window_start + visible_count]
        if len(window) < 2:
            return

        # Draw at most ~2 points per screen pixel to keep painting cost bounded.
        max_points = max(width * 2, 256)
        if len(window) > max_points:
            step = (len(window) + max_points - 1) // max_points
            downsampled = window[::step]
            if downsampled[-1] != window[-1]:
                downsampled.append(window[-1])
            window = downsampled

        # Map volts to vertical pixels using 8 vertical divisions (+/-4 around center).
        volts_full_scale = max(v_div * 4.0, 1e-6)  # +/-4 divisions on vertical.
        x_scale = width / max(len(window) - 1, 1)

        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)

        points: list[QPointF] = []
        for i, volts in enumerate(window):
            x = float(i) * x_scale
            # Screen Y axis is inverted: larger voltage means lower pixel y value.
            y = (height / 2.0) - (volts / volts_full_scale) * (height / 2.0)
            points.append(QPointF(x, y))
        painter.drawPolyline(points)

    def _choose_window_start(
        self, samples: list[float], visible_count: int, source_name: str
    ) -> int:
        # If trigger source doesn't match this channel, just show latest window.
        if self.settings.trigger_source != source_name:
            return max(0, len(samples) - visible_count)

        # If firmware provides explicit trigger metadata, use it directly.
        # This is preferred because firmware sees the true trigger engine state.
        if self.frame_data and self.frame_data.trigger_found is not None:
            if not self.frame_data.trigger_found:
                self._trigger_found = False
                return max(0, len(samples) - visible_count)
            if self.frame_data.trigger_index is None:
                self._trigger_found = False
                return max(0, len(samples) - visible_count)
            # Place trigger around 20% from left, similar to many scopes.
            left_margin = int(0.2 * visible_count)
            self._trigger_found = True
            return max(
                0,
                min(
                    int(self.frame_data.trigger_index) - left_margin,
                    len(samples) - visible_count,
                ),
            )

        # Fallback trigger detection: choose the newest rising crossing that can
        # be displayed at a fixed trigger position.
        level = self.settings.trigger_level_v
        hysteresis = 0.02  # volts
        min_rise_v = 0.0  # enforce only positive slope
        low = level - (hysteresis * 0.5)
        left_margin = int(0.2 * visible_count)
        max_crossing = len(samples) - (visible_count - left_margin)
        if max_crossing <= left_margin:
            return max(0, len(samples) - visible_count)

        for i in range(max_crossing, left_margin, -1):
            dv = samples[i] - samples[i - 1]
            if samples[i - 1] <= low and samples[i] >= level and dv > min_rise_v:
                self._trigger_found = True
                return max(0, min(i - left_margin, len(samples) - visible_count))

        self._trigger_found = False
        return max(0, len(samples) - visible_count)

    def _compute_visible_count(self, sample_count: int) -> int:
        # Decide which section of the frame is visible based on time/div setting.
        # 10 horizontal divisions means total visible time = 10 * s_div.
        visible_seconds = 10.0 * self.settings.s_div
        # Sample rate travels with each frame so display math always matches source.
        sample_rate = self.frame_data.sample_rate_hz if self.frame_data else 1
        visible_count = int(round(visible_seconds * sample_rate))
        visible_count = max(
            2, min(sample_count, visible_count if visible_count > 0 else sample_count)
        )
        return visible_count

    @staticmethod
    def _format_time_div(seconds_per_div: float) -> str:
        us = seconds_per_div * 1e6
        if us >= 1000.0:
            ms = us / 1000.0
            if abs(ms - round(ms)) < 1e-9:
                return f"{int(round(ms))} ms/div"
            return f"{ms:.1f} ms/div"
        if abs(us - round(us)) < 1e-9:
            return f"{int(round(us))} us/div"
        return f"{us:.1f} us/div"


class MainWindow(QMainWindow):
    """
    Main oscilloscope GUI shell.

    It owns:
    - Controls (buttons/combos/sliders)
    - Acquisition timer
    - Active data source (mock or USB backend)
    - Waveform widget
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("oscope-GUI")
        self.resize(1180, 720)
        self.setMinimumSize(980, 620)

        self.settings = ScopeSettings()
        self.status_label = QLabel("Disconnected")

        # Current backend connection (set on Connect).
        self.source: ScopeSource | None = None
        # Polling timer drives the frame-by-frame acquisition loop.
        self.acq_timer = QTimer(self)
        self.acq_timer.setInterval(10)  # Faster polling keeps USB backlog lower.
        self.acq_timer.timeout.connect(self._poll_next_frame)

        self._build_layout()

    def _build_layout(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        # Top row: data source + transport controls.
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        top_bar.addWidget(QLabel("Source:"))

        self.device_combo = QComboBox()
        self.device_combo.addItems(["Mock Device", "USB Scope"])
        self.device_combo.setMinimumWidth(220)
        top_bar.addWidget(self.device_combo)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._connect_source)
        top_bar.addWidget(self.connect_btn)

        self.run_btn = QPushButton("Run")
        self.run_btn.setEnabled(False)
        # Run toggles continuous timer-driven polling.
        self.run_btn.clicked.connect(self._toggle_run)
        top_bar.addWidget(self.run_btn)

        self.single_btn = QPushButton("Single")
        self.single_btn.setEnabled(False)
        # Single requests one frame and then stops.
        self.single_btn.clicked.connect(self._single_shot)
        top_bar.addWidget(self.single_btn)

        top_bar.addStretch(1)
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        top_bar.addWidget(self.status_label)
        layout.addLayout(top_bar)

        # Middle row currently contains waveform display only.
        # Keep this layout split-friendly if you re-enable control panel later.
        middle = QHBoxLayout()
        middle.setSpacing(10)
        layout.addLayout(middle, stretch=1)

        self.waveform = WaveformWidget()
        self.waveform.set_settings(self.settings)
        middle.addWidget(self.waveform, stretch=1)

        footer = QLabel(
            "Viewer mode: waveform display follows front-panel settings from scope firmware."
        )
        layout.addWidget(footer)

    def _build_control_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        box = QVBoxLayout(panel)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        title = QLabel("Front Panel Controls")
        box.addWidget(title)

        # The user mentioned "4 buttons". Here we expose CH toggles + run controls.
        # You can repurpose/rename these later to match physical front panel labels.
        self.ch1_btn = QPushButton("CH1 On")
        self.ch1_btn.setCheckable(True)
        self.ch1_btn.setChecked(True)
        self.ch1_btn.toggled.connect(
            lambda checked: self._set_channel_enabled(1, checked)
        )
        box.addWidget(self.ch1_btn)

        self.ch2_btn = QPushButton("CH2 On")
        self.ch2_btn.setCheckable(True)
        self.ch2_btn.setChecked(True)
        self.ch2_btn.toggled.connect(
            lambda checked: self._set_channel_enabled(2, checked)
        )
        box.addWidget(self.ch2_btn)

        self.hold_btn = QPushButton("Hold")
        self.hold_btn.clicked.connect(self._stop_run)
        box.addWidget(self.hold_btn)

        self.auto_btn = QPushButton("Auto")
        self.auto_btn.clicked.connect(self._single_shot)
        box.addWidget(self.auto_btn)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        box.addLayout(grid)

        # Per-channel volts/div controls.
        grid.addWidget(QLabel("CH1 V/div"), 0, 0)
        self.ch1_vdiv_combo = QComboBox()
        self.ch1_vdiv_combo.addItems(["0.20", "0.50", "1.00", "2.00", "5.00"])
        self.ch1_vdiv_combo.setCurrentText("1.00")
        self.ch1_vdiv_combo.currentTextChanged.connect(
            self._sync_settings_from_controls
        )
        grid.addWidget(self.ch1_vdiv_combo, 0, 1)

        grid.addWidget(QLabel("CH2 V/div"), 1, 0)
        self.ch2_vdiv_combo = QComboBox()
        self.ch2_vdiv_combo.addItems(["0.20", "0.50", "1.00", "2.00", "5.00"])
        self.ch2_vdiv_combo.setCurrentText("1.00")
        self.ch2_vdiv_combo.currentTextChanged.connect(
            self._sync_settings_from_controls
        )
        grid.addWidget(self.ch2_vdiv_combo, 1, 1)

        # Global seconds/div control.
        grid.addWidget(QLabel("S/div"), 2, 0)
        self.sdiv_combo = QComboBox()
        self.sdiv_combo.addItems(
            ["50 us", "100 us", "200 us", "500 us", "1 ms", "2 ms", "5 ms", "10 ms"]
        )
        self.sdiv_combo.setCurrentText("200 us")
        self.sdiv_combo.currentTextChanged.connect(self._sync_settings_from_controls)
        grid.addWidget(self.sdiv_combo, 2, 1)

        # Trigger source and trigger level.
        grid.addWidget(QLabel("Trigger Src"), 3, 0)
        self.trigger_src_combo = QComboBox()
        self.trigger_src_combo.addItems(["CH1", "CH2"])
        self.trigger_src_combo.currentTextChanged.connect(
            self._sync_settings_from_controls
        )
        grid.addWidget(self.trigger_src_combo, 3, 1)

        grid.addWidget(QLabel("Trigger (V)"), 4, 0)
        self.trigger_value_label = QLabel("0.00")
        grid.addWidget(self.trigger_value_label, 4, 1)

        self.trigger_slider = QSlider(Qt.Orientation.Horizontal)
        self.trigger_slider.setRange(-500, 500)  # -5.00V..+5.00V in 10mV steps.
        self.trigger_slider.setValue(0)
        self.trigger_slider.valueChanged.connect(self._sync_settings_from_controls)
        box.addWidget(self.trigger_slider)

        box.addStretch(1)
        return panel

    def _connect_source(self) -> None:
        # Stop any ongoing acquisition before switching source.
        self._stop_run()
        if self.source is not None:
            # Ensure stale handles are released before opening a new backend.
            self.source.disconnect()

        selected = self.device_combo.currentText()
        self.source = MockScope() if selected == "Mock Device" else UsbScope()

        try:
            self.source.connect()
        except Exception as exc:
            self.status_label.setText(f"Connect failed: {exc}")
            self.run_btn.setEnabled(False)
            self.single_btn.setEnabled(False)
            return

        self.status_label.setText(f"Connected: {selected}")
        self.run_btn.setEnabled(True)
        self.single_btn.setEnabled(True)
        # Pull one frame immediately so user sees feedback after connect.
        self._single_shot()

    def _toggle_run(self) -> None:
        if self.acq_timer.isActive():
            self._stop_run()
            return
        if self.source is None:
            self.status_label.setText("Connect a source first")
            return

        # Start periodic acquisition. Each timer event reads one frame.
        self.acq_timer.start()
        self.run_btn.setText("Stop")
        self.status_label.setText("Running (frame by frame)")

    def _stop_run(self) -> None:
        self.acq_timer.stop()
        self.run_btn.setText("Run")

    def _single_shot(self) -> None:
        # One frame capture, useful for debugging trigger/settings.
        self._stop_run()
        self._poll_next_frame()

    def _poll_next_frame(self) -> None:
        if self.source is None:
            return
        try:
            # The backend contract is "one complete frame per call".
            frame = self.source.get_next_frame()
        except NotImplementedError as exc:
            self._stop_run()
            self.status_label.setText(str(exc))
            return
        except Exception as exc:
            # USB startup/transport hiccups are expected; keep running and retry.
            msg = str(exc)
            if ("Waiting for first scope frame" in msg) or (
                "No valid scope frames received" in msg
            ):
                self.status_label.setText(msg)
                return
            self._stop_run()
            self.status_label.setText(f"Acq error: {exc}")
            return

        # Apply any settings mirrored from firmware/front-panel state.
        self._apply_frame_settings(frame)
        self.waveform.set_frame(frame)
        if frame.trigger_found is False:
            self.status_label.setText(
                f"Searching trigger ({frame.sample_count} samples/ch @ {frame.sample_rate_hz} Hz)"
            )
        else:
            self.status_label.setText(
                f"Running: {frame.sample_count} samples/ch @ {frame.sample_rate_hz} Hz"
            )

    def _apply_frame_settings(self, frame: ScopeFrame) -> None:
        # Firmware may send control settings with each frame.
        # Use them when present so UI reflects physical knobs/buttons.
        if frame.ch1_enabled is not None:
            self.settings.ch1_enabled = frame.ch1_enabled
        if frame.ch2_enabled is not None:
            self.settings.ch2_enabled = frame.ch2_enabled
        if frame.ch1_v_div is not None:
            self.settings.ch1_v_div = frame.ch1_v_div
        if frame.ch2_v_div is not None:
            self.settings.ch2_v_div = frame.ch2_v_div
        if frame.s_div is not None:
            self.settings.s_div = frame.s_div
        if frame.trigger_source is not None:
            self.settings.trigger_source = frame.trigger_source
        if frame.trigger_level_v is not None:
            self.settings.trigger_level_v = frame.trigger_level_v
        self.waveform.set_settings(self.settings)

    @staticmethod
    def _parse_sdiv_text(text: str) -> float:
        # Control text is kept human-readable; convert it to seconds/div.
        value_text, unit = text.split()
        value = float(value_text)
        if unit == "us":
            return value * 1e-6
        if unit == "ms":
            return value * 1e-3
        raise ValueError(f"Unsupported time unit: {unit}")
