from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPen
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

from backend.mock_scope import MockScope
from backend.scope_source import ScopeFrame, ScopeSource
from backend.usb_scope import UsbScope


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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(360)
        self.frame_data: ScopeFrame | None = None
        self.settings = ScopeSettings()

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        painter.fillRect(rect, QColor("#10131A"))
        self._draw_grid(painter, rect.width(), rect.height())

        # Draw trigger level even before data exists so control feedback is visible.
        self._draw_trigger_line(painter, rect.width(), rect.height())

        if self.frame_data is None or self.frame_data.sample_count < 2:
            painter.setPen(QColor("#7F8FA6"))
            painter.drawText(14, 20, "No frame yet. Click Connect, then Run.")
            return

        if self.settings.ch1_enabled:
            self._draw_channel(
                painter=painter,
                samples=self.frame_data.ch1,
                width=rect.width(),
                height=rect.height(),
                v_div=self.settings.ch1_v_div,
                color=QColor("#4EE39B"),
                source_name="CH1",
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
            )

        painter.setPen(QColor("#7F8FA6"))
        painter.drawText(
            14,
            20,
            (
                f"CH1 {self.settings.ch1_v_div:.2f} V/div   "
                f"CH2 {self.settings.ch2_v_div:.2f} V/div   "
                f"{self.settings.s_div * 1e6:.0f} us/div"
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

    def _draw_trigger_line(self, painter: QPainter, width: int, height: int) -> None:
        # Trigger level uses CH1 vertical scale for now (common in simple scopes).
        v_div = self.settings.ch1_v_div
        volts_full_scale = max(v_div * 4.0, 1e-6)  # +/-4 divisions around center.
        y = height / 2.0 - (self.settings.trigger_level_v / volts_full_scale) * (height / 2.0)

        trigger_pen = QPen(QColor("#FFB347"))
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
    ) -> None:
        # Decide which section of the frame is visible based on time/div setting.
        # 10 horizontal divisions means total visible time = 10 * s_div.
        visible_seconds = 10.0 * self.settings.s_div
        sample_rate = self.frame_data.sample_rate_hz if self.frame_data else 1
        visible_count = int(visible_seconds * sample_rate)
        visible_count = max(2, min(len(samples), visible_count if visible_count > 0 else len(samples)))

        # Trigger alignment: try to start window near a threshold crossing so each
        # repaint is stable and looks like a scope instead of random drift.
        window_start = self._choose_window_start(samples, visible_count, source_name)
        window = samples[window_start : window_start + visible_count]
        if len(window) < 2:
            return

        volts_full_scale = max(v_div * 4.0, 1e-6)  # +/-4 divisions on vertical.
        x_scale = width / max(len(window) - 1, 1)

        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)

        points: list[QPointF] = []
        for i, volts in enumerate(window):
            x = float(i) * x_scale
            y = (height / 2.0) - (volts / volts_full_scale) * (height / 2.0)
            points.append(QPointF(x, y))
        painter.drawPolyline(points)

    def _choose_window_start(self, samples: list[float], visible_count: int, source_name: str) -> int:
        # If trigger source doesn't match this channel, just show latest window.
        if self.settings.trigger_source != source_name:
            return max(0, len(samples) - visible_count)

        # If firmware provided trigger index, trust it.
        if self.frame_data and self.frame_data.trigger_index is not None:
            # Place trigger around 20% from left, similar to many scopes.
            left_margin = int(0.2 * visible_count)
            return max(0, min(self.frame_data.trigger_index - left_margin, len(samples) - visible_count))

        # Fallback trigger detection: first rising crossing of trigger level.
        level = self.settings.trigger_level_v
        left_margin = int(0.2 * visible_count)
        for i in range(1, len(samples)):
            if samples[i - 1] < level <= samples[i]:
                return max(0, min(i - left_margin, len(samples) - visible_count))

        # If no crossing was found, show latest portion.
        return max(0, len(samples) - visible_count)


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

        self.source: ScopeSource | None = None
        self.acq_timer = QTimer(self)
        self.acq_timer.setInterval(33)  # ~30 FPS UI refresh.
        self.acq_timer.timeout.connect(self._poll_next_frame)

        self._build_layout()

    def _build_layout(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        # Top row: transport/data source controls.
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        top_bar.addWidget(QLabel("Source:"))

        self.device_combo = QComboBox()
        self.device_combo.addItems(["Mock Device", "USB Scope (stub)"])
        self.device_combo.setMinimumWidth(220)
        top_bar.addWidget(self.device_combo)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._connect_source)
        top_bar.addWidget(self.connect_btn)

        self.run_btn = QPushButton("Run")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._toggle_run)
        top_bar.addWidget(self.run_btn)

        self.single_btn = QPushButton("Single")
        self.single_btn.setEnabled(False)
        self.single_btn.clicked.connect(self._single_shot)
        top_bar.addWidget(self.single_btn)

        top_bar.addStretch(1)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top_bar.addWidget(self.status_label)
        layout.addLayout(top_bar)

        # Middle row: left control panel + right waveform.
        middle = QHBoxLayout()
        middle.setSpacing(10)
        layout.addLayout(middle, stretch=1)

        control_panel = self._build_control_panel()
        control_panel.setFixedWidth(310)
        middle.addWidget(control_panel)

        self.waveform = WaveformWidget()
        self.waveform.set_settings(self.settings)
        middle.addWidget(self.waveform, stretch=1)

        footer = QLabel(
            "Frame-based UI is active. Mock source emulates two simultaneous channels; USB backend is a stub."
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
        self.ch1_btn.toggled.connect(lambda checked: self._set_channel_enabled(1, checked))
        box.addWidget(self.ch1_btn)

        self.ch2_btn = QPushButton("CH2 On")
        self.ch2_btn.setCheckable(True)
        self.ch2_btn.setChecked(True)
        self.ch2_btn.toggled.connect(lambda checked: self._set_channel_enabled(2, checked))
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
        self.ch1_vdiv_combo.currentTextChanged.connect(self._sync_settings_from_controls)
        grid.addWidget(self.ch1_vdiv_combo, 0, 1)

        grid.addWidget(QLabel("CH2 V/div"), 1, 0)
        self.ch2_vdiv_combo = QComboBox()
        self.ch2_vdiv_combo.addItems(["0.20", "0.50", "1.00", "2.00", "5.00"])
        self.ch2_vdiv_combo.setCurrentText("1.00")
        self.ch2_vdiv_combo.currentTextChanged.connect(self._sync_settings_from_controls)
        grid.addWidget(self.ch2_vdiv_combo, 1, 1)

        # Global seconds/div control.
        grid.addWidget(QLabel("S/div"), 2, 0)
        self.sdiv_combo = QComboBox()
        self.sdiv_combo.addItems(["50 us", "100 us", "200 us", "500 us", "1 ms", "2 ms", "5 ms", "10 ms"])
        self.sdiv_combo.setCurrentText("200 us")
        self.sdiv_combo.currentTextChanged.connect(self._sync_settings_from_controls)
        grid.addWidget(self.sdiv_combo, 2, 1)

        # Trigger source and trigger level.
        grid.addWidget(QLabel("Trigger Src"), 3, 0)
        self.trigger_src_combo = QComboBox()
        self.trigger_src_combo.addItems(["CH1", "CH2"])
        self.trigger_src_combo.currentTextChanged.connect(self._sync_settings_from_controls)
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
        self._single_shot()

    def _toggle_run(self) -> None:
        if self.acq_timer.isActive():
            self._stop_run()
            return
        if self.source is None:
            self.status_label.setText("Connect a source first")
            return

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
            frame = self.source.get_next_frame()
        except NotImplementedError as exc:
            self._stop_run()
            self.status_label.setText(str(exc))
            return
        except Exception as exc:
            self._stop_run()
            self.status_label.setText(f"Acq error: {exc}")
            return

        self.waveform.set_frame(frame)
        self.status_label.setText(f"Running: {frame.sample_count} samples/ch @ {frame.sample_rate_hz} Hz")

    def _set_channel_enabled(self, channel: int, enabled: bool) -> None:
        if channel == 1:
            self.settings.ch1_enabled = enabled
            self.ch1_btn.setText("CH1 On" if enabled else "CH1 Off")
        else:
            self.settings.ch2_enabled = enabled
            self.ch2_btn.setText("CH2 On" if enabled else "CH2 Off")
        self.waveform.set_settings(self.settings)

    def _sync_settings_from_controls(self) -> None:
        # Parse control values into normalized SI units.
        self.settings.ch1_v_div = float(self.ch1_vdiv_combo.currentText())
        self.settings.ch2_v_div = float(self.ch2_vdiv_combo.currentText())
        self.settings.s_div = self._parse_sdiv_text(self.sdiv_combo.currentText())
        self.settings.trigger_source = self.trigger_src_combo.currentText()
        self.settings.trigger_level_v = self.trigger_slider.value() / 100.0
        self.trigger_value_label.setText(f"{self.settings.trigger_level_v:.2f}")
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
