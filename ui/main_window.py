from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.mock_scope import MockScope
from backend.scope_source import ScopeFrame, ScopeSource
from backend.usb_scope import UsbScope
from PyQt6.QtCore import Qt, QTimer
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

try:
    import fastplotlib as fpl
except ImportError:  # pragma: no cover - optional dependency at runtime
    fpl = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency at runtime
    np = None

HORIZONTAL_DIVISIONS = 10
VERTICAL_DIVISIONS = 10
X_AXIS_MIN = -HORIZONTAL_DIVISIONS / 2.0
X_AXIS_MAX = HORIZONTAL_DIVISIONS / 2.0
Y_AXIS_CENTER = 5.0
Y_AXIS_MIN = Y_AXIS_CENTER - (VERTICAL_DIVISIONS / 2.0)
Y_AXIS_MAX = Y_AXIS_CENTER + (VERTICAL_DIVISIONS / 2.0)


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
    Scope display hosted inside a fastplotlib figure.

    The widget still owns oscilloscope-specific view logic such as trigger
    anchoring and timebase windowing, but delegates the actual drawing to the
    GPU-backed plotting canvas.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(360)
        self.frame_data: ScopeFrame | None = None
        self.settings = ScopeSettings()
        self._trigger_found: bool = False
        self._trigger_window_start: int | None = None
        self._backend_available = fpl is not None and np is not None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.readout_label = QLabel()
        self.readout_label.setStyleSheet(
            "color: #7F8FA6; background: rgba(16, 19, 26, 190);"
            "padding: 5px 8px; border: 1px solid rgba(29, 38, 51, 180); border-radius: 4px;"
        )
        self.readout_label.setParent(self)
        self.readout_label.raise_()

        self.notice_label = QLabel()
        self.notice_label.setStyleSheet(
            "color: #7F8FA6; background: rgba(16, 19, 26, 190);"
            "padding: 5px 8px; border: 1px solid rgba(29, 38, 51, 180); border-radius: 4px;"
        )
        self.notice_label.setParent(self)
        self.notice_label.raise_()

        self.figure = None
        self.subplot = None
        self._plot_widget = None
        self._bounds_line = None
        self._trigger_line = None
        self._ch1_line = None
        self._ch2_line = None

        if self._backend_available:
            self._init_fastplotlib_canvas(layout)
        else:
            self.notice_label.setText(
                "Install `fastplotlib` and `numpy` to enable waveform rendering."
            )

        self._update_readout()

    def _init_fastplotlib_canvas(self, layout: QVBoxLayout) -> None:
        assert fpl is not None
        assert np is not None

        self.figure = fpl.Figure()
        self.subplot = self.figure[0, 0]
        self.subplot.name = None
        self.subplot.title = ""
        self.subplot.camera.maintain_aspect = False
        self.subplot.background_color = ((0.062, 0.074, 0.102, 1.0),) * 4
        self.subplot.axes.grids.xy.visible = True
        self.subplot.axes.auto_grid = False
        self.subplot.axes.grids.xy.major_step = (1, 1)
        self.subplot.axes.grids.xy.minor_step = (0, 0)
        self.subplot.axes.grids.xy.major_thickness = 0.6
        self.subplot.axes.grids.xy.axis_thickness = 0.35
        self.subplot.axes.grids.xy.major_color = "#18202C"
        self.subplot.axes.grids.xy.axis_color = "#18202C"
        self.subplot.axes.x.visible = False
        self.subplot.axes.y.visible = False
        self.subplot.axes.z.visible = False

        bounds = np.asarray(
            [
                [X_AXIS_MIN, Y_AXIS_MIN, 0.0],
                [X_AXIS_MAX, Y_AXIS_MAX, 0.0],
            ],
            dtype=np.float32,
        )
        baseline = self._make_division_line([0.0, 0.0], [-40.0, -40.0])
        trigger = self._make_division_line([0.0, 0.0], [0.0, 0.0])

        self._bounds_line = self.subplot.add_line(
            data=bounds,
            colors=(0.0, 0.0, 0.0, 0.0),
            thickness=1.0,
            name="bounds",
        )
        self._trigger_line = self.subplot.add_line(
            data=trigger,
            colors="white",
            thickness=0.9,
            name="trigger",
        )
        self._ch1_line = self.subplot.add_line(
            data=baseline,
            colors="#FFD84D",
            thickness=1.4,
            name="ch1",
        )
        self._ch2_line = self.subplot.add_line(
            data=baseline,
            colors="#65B7FF",
            thickness=1.4,
            name="ch2",
        )

        self._plot_widget = self.figure.show(
            autoscale=False,
            maintain_aspect=False,
            axes_visible=True,
        )
        self._plot_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._plot_widget, stretch=1)
        self._plot_widget.lower()
        self.subplot.controller.enabled = False
        self._apply_default_view()
        self._reposition_overlays()

    @staticmethod
    def _make_division_line(xs: list[float], ys: list[float]) -> "np.ndarray":
        assert np is not None
        return np.column_stack(
            [
                np.asarray(xs, dtype=np.float32),
                np.asarray(ys, dtype=np.float32),
                np.zeros(len(xs), dtype=np.float32),
            ]
        )

    def set_frame(self, frame: ScopeFrame) -> None:
        self.frame_data = frame
        self._refresh_plot()

    def set_settings(self, settings: ScopeSettings) -> None:
        self.settings = settings
        self._refresh_plot()

    def _refresh_plot(self) -> None:
        self._update_readout()
        self._update_trigger_line()

        if not self._backend_available or self.frame_data is None:
            self._update_notice()
            return
        if self.frame_data.sample_count < 2:
            self._set_channel_visible(self._ch1_line, False)
            self._set_channel_visible(self._ch2_line, False)
            self._update_notice()
            return

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

        self._ch1_line = self._update_channel_graphic(
            graphic=self._ch1_line,
            samples=self.frame_data.ch1,
            v_div=self.settings.ch1_v_div,
            source_name="CH1",
            enabled=self.settings.ch1_enabled,
            forced_window_start=shared_window_start,
            color="#FFD84D",
            name="ch1",
        )
        self._ch2_line = self._update_channel_graphic(
            graphic=self._ch2_line,
            samples=self.frame_data.ch2,
            v_div=self.settings.ch2_v_div,
            source_name="CH2",
            enabled=self.settings.ch2_enabled,
            forced_window_start=shared_window_start,
            color="#65B7FF",
            name="ch2",
        )
        self._update_notice()

    def _update_notice(self) -> None:
        if not self._backend_available:
            self.notice_label.show()
            return
        if self.frame_data is None or self.frame_data.sample_count < 2:
            self.notice_label.setText("No frame yet. Click Connect, then Run.")
            self.notice_label.adjustSize()
            self.notice_label.show()
            self._reposition_overlays()
            return
        if self._trigger_found:
            self.notice_label.hide()
            return
        self.notice_label.setText("Trigger not found in current frame.")
        self.notice_label.adjustSize()
        self.notice_label.show()
        self._reposition_overlays()

    def _update_readout(self) -> None:
        self.readout_label.setText(
            (
                f"CH1 {self.settings.ch1_v_div:.2f} V/div   "
                f"CH2 {self.settings.ch2_v_div:.2f} V/div   "
                f"{self._format_time_div(self.settings.s_div)}   "
                f"Trig {self.settings.trigger_source} {self.settings.trigger_level_v:+.2f} V"
            )
        )
        self.readout_label.adjustSize()
        self.readout_label.show()
        self._reposition_overlays()

    def _update_trigger_line(self) -> None:
        if not self._backend_available or self._trigger_line is None:
            return

        v_div = (
            self.settings.ch2_v_div
            if self.settings.trigger_source == "CH2"
            else self.settings.ch1_v_div
        )
        y_divisions = self.settings.trigger_level_v / max(v_div, 1e-6)
        self._trigger_line.data = self._make_division_line(
            [X_AXIS_MIN, X_AXIS_MAX],
            [Y_AXIS_CENTER + y_divisions, Y_AXIS_CENTER + y_divisions],
        )
        self._trigger_line.colors = (
            "#65B7FF" if self.settings.trigger_source == "CH2" else "#FFD84D"
        )

    @staticmethod
    def _set_channel_visible(graphic: object, visible: bool) -> None:
        if graphic is not None:
            graphic.visible = visible

    def _update_channel_graphic(
        self,
        graphic: object,
        samples: list[float],
        v_div: float,
        source_name: str,
        enabled: bool,
        forced_window_start: int | None = None,
        color: str = "white",
        name: str | None = None,
    ) -> object:
        if graphic is None:
            return graphic

        graphic.visible = enabled
        if not enabled:
            return graphic

        points = self._build_channel_points(
            samples=samples,
            v_div=v_div,
            source_name=source_name,
            forced_window_start=forced_window_start,
        )
        if points is None:
            graphic.visible = False
            return graphic

        current_shape = getattr(getattr(graphic, "data", None), "value", None)
        if current_shape is not None:
            current_shape = current_shape.shape

        if current_shape != points.shape:
            assert self.subplot is not None
            self.subplot.delete_graphic(graphic)
            graphic = self.subplot.add_line(
                data=points,
                colors=color,
                thickness=1.4,
                name=name,
            )
            graphic.visible = enabled
            return graphic

        graphic.data = points
        return graphic

    def _build_channel_points(
        self,
        samples: list[float],
        v_div: float,
        source_name: str,
        forced_window_start: int | None = None,
    ) -> "np.ndarray | None":
        assert np is not None

        visible_count = self._compute_visible_count(len(samples))
        if forced_window_start is None:
            window_start = self._choose_window_start(samples, visible_count, source_name)
        else:
            window_start = forced_window_start

        if visible_count < len(samples):
            last = len(samples) - 1
            if visible_count < 2 or last < 1:
                return None
            positions = np.linspace(0.0, float(last), visible_count, dtype=np.float32)
            base = np.arange(len(samples), dtype=np.float32)
            window = np.interp(
                positions,
                base,
                np.asarray(samples, dtype=np.float32),
            )
        else:
            window = np.asarray(
                samples[window_start : window_start + visible_count],
                dtype=np.float32,
            )
            if window.size < 2:
                return None

        render_count = min(max(window.size, 256), 2048)
        if window.size > render_count:
            sample_idx = np.linspace(
                0.0, float(window.size - 1), render_count, dtype=np.float32
            )
            window = np.interp(sample_idx, np.arange(window.size, dtype=np.float32), window)
        elif window.size < render_count:
            sample_idx = np.linspace(
                0.0, float(window.size - 1), render_count, dtype=np.float32
            )
            window = np.interp(sample_idx, np.arange(window.size, dtype=np.float32), window)

        x = np.linspace(X_AXIS_MIN, X_AXIS_MAX, window.size, dtype=np.float32)
        y = Y_AXIS_CENTER + (window / max(v_div, 1e-6))
        return np.column_stack(
            [x, y.astype(np.float32), np.zeros(window.size, dtype=np.float32)]
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        margin = 10
        self.readout_label.move(margin, margin)
        if self.notice_label.isVisible():
            y = self.readout_label.y() + self.readout_label.height() + 6
            self.notice_label.move(margin, y)

    def _apply_default_view(self) -> None:
        if self.subplot is None:
            return

        self.subplot.camera.width = HORIZONTAL_DIVISIONS
        self.subplot.camera.height = VERTICAL_DIVISIONS
        self.subplot.camera.zoom = 1.0
        self.subplot.camera.local.position = (
            (X_AXIS_MIN + X_AXIS_MAX) / 2.0,
            Y_AXIS_CENTER,
            0.0,
        )

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
                self._trigger_window_start = None
                return max(0, len(samples) - visible_count)
            if self.frame_data.trigger_index is None:
                self._trigger_found = False
                self._trigger_window_start = None
                return max(0, len(samples) - visible_count)
            # Place trigger around 20% from left, similar to many scopes.
            left_margin = int(0.2 * visible_count)
            self._trigger_found = True
            candidate = max(
                0,
                min(
                    int(self.frame_data.trigger_index) - left_margin,
                    len(samples) - visible_count,
                ),
            )
            if self._trigger_window_start is not None:
                delta = candidate - self._trigger_window_start
                abs_delta = abs(delta)
                # Suppress sub-sample/index chatter around a stable edge.
                if abs_delta <= 2:
                    candidate = self._trigger_window_start
                # Damp medium jumps to reduce visible jitter.
                elif abs_delta <= 24:
                    candidate = self._trigger_window_start + (delta // 2)
            self._trigger_window_start = candidate
            return candidate

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
        self._trigger_window_start = None
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
        ns = seconds_per_div * 1e9
        if ns < 1000.0:
            if abs(ns - round(ns)) < 1e-9:
                return f"{int(round(ns))} ns/div"
            return f"{ns:.1f} ns/div"
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
            "Viewer mode: fastplotlib renders the scope display while firmware settings still drive the view."
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
            [
                "1 us",
                "2 us",
                "5 us",
                "10 us",
                "20 us",
                "50 us",
                "100 us",
                "200 us",
                "500 us",
                "1 ms",
                "2 ms",
                "5 ms",
                "10 ms",
                "20 ms",
                "50 ms",
                "100 ms",
            ]
        )
        self.sdiv_combo.setCurrentText("20 us")
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
        if unit == "ns":
            return value * 1e-9
        if unit == "us":
            return value * 1e-6
        if unit == "ms":
            return value * 1e-3
        raise ValueError(f"Unsupported time unit: {unit}")
