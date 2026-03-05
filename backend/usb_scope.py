from __future__ import annotations

import struct
import time
from typing import Any

from backend import protocol
from backend.scope_source import ScopeFrame

try:
    import numpy as _np
except Exception:
    _np = None


class UsbScope:
    """
    Real USB transport stub.

    This class is intentionally shaped the same as MockScope so the UI can switch
    sources without changing plotting or control logic.
    """

    def __init__(self) -> None:
        self._connected = False
        self._serial: Any | None = None
        self._last_sent: tuple[float, float, float] | None = None
        self._rx = bytearray()
        self._port_name: str | None = None
        self._last_frame: ScopeFrame | None = None
        self._connected_at: float = 0.0
        # TODO (firmware integration):
        # Store VID/PID, endpoint numbers, timeout settings, etc.
        # Example fields once you define protocol:
        # self.vid = 0xXXXX
        # self.pid = 0xXXXX
        # self.ep_in = 0x81
        # self.ep_out = 0x01

    def connect(self) -> None:
        """
        Open USB device and claim interface.

        Replace with PyUSB/libusb or your preferred transport when the STM32
        protocol is ready.
        """
        # Open the most likely serial CDC port. Do not hard-fail on descriptor
        # naming differences (usbserial/ttyACM/cu.* all appear in practice).
        try:
            import serial  # type: ignore
            import serial.tools.list_ports  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"PySerial import failed: {exc}") from exc

        ports = list(serial.tools.list_ports.comports())
        if not ports:
            raise RuntimeError("No serial ports found")

        def port_score(p: Any) -> int:
            text = f"{p.device} {p.description} {p.manufacturer}".lower()
            score = 0
            hints = [
                "stm32",
                "cdc",
                "usbmodem",
                "usbserial",
                "ttyacm",
                "wch",
                "ch340",
                "cp210",
            ]
            for h in hints:
                if h in text:
                    score += 10
            if "bluetooth" in text:
                score -= 30
            return score

        last_error: Exception | None = None
        for port in sorted(ports, key=port_score, reverse=True):
            try:
                ser = serial.Serial(port.device, baudrate=115200, timeout=0)
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                self._serial = ser
                self._port_name = port.device
                break
            except Exception as exc:
                last_error = exc

        if self._serial is None:
            port_list = ", ".join(p.device for p in ports)
            msg = f"Failed to open any serial port: {port_list}"
            if last_error is not None:
                msg += f" ({last_error})"
            raise RuntimeError(msg)
        self._connected = True
        self._connected_at = time.monotonic()

    def disconnect(self) -> None:
        """Release USB resources."""
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        self._connected = False
        self._last_sent = None
        self._rx.clear()
        self._port_name = None
        self._last_frame = None
        self._connected_at = 0.0

    def set_front_panel(self, ch1_v_div: float, ch2_v_div: float, s_div: float) -> None:
        """
        Push CH1/CH2 V/div and S/div to firmware.

        This is a no-op if no serial transport is available yet.
        """
        if not self._connected:
            return

        values = (ch1_v_div, ch2_v_div, s_div)
        if values == self._last_sent:
            return
        self._last_sent = values

        if self._serial is None:
            return
        self._serial.write(protocol.encode_set_ch1_vdiv(ch1_v_div))
        self._serial.write(protocol.encode_set_ch2_vdiv(ch2_v_div))
        self._serial.write(protocol.encode_set_sdiv(s_div))

    def get_next_frame(self) -> ScopeFrame:
        """
        Read and decode one full acquisition frame from USB.

        Expected future steps:
        1. Read fixed-size frame header.
        2. Validate magic/version.
        3. Read payload for both channels.
        4. Convert packed ADC values into volts.
        5. Return ScopeFrame(sample_rate_hz, ch1, ch2, trigger_index?).
        """
        if not self._connected:
            raise RuntimeError("UsbScope is not connected")
        if self._serial is None:
            raise RuntimeError("USB serial transport is unavailable")

        # Non-blocking ingest/parsing to keep UI responsive.
        self._ingest_available()
        latest: tuple[bytes, bytes] | None = None
        while True:
            pkt = self._try_extract_one_packet()
            if pkt is None:
                break
            latest = pkt

        if latest is None:
            if self._last_frame is not None:
                return self._last_frame
            if (time.monotonic() - self._connected_at) < 2.0:
                raise RuntimeError("Waiting for first scope frame")
            suffix = f" on {self._port_name}" if self._port_name else ""
            raise RuntimeError(f"No valid scope frames received{suffix}")

        self._last_frame = self._decode_frame(*latest)
        return self._last_frame

    def _decode_frame(self, header: bytes, payload: bytes) -> ScopeFrame:
        _, header_size, sample_count, trigger, ch_config, time_config, reserved = (
            struct.unpack(protocol.FRAME_HDR_FMT, header)
        )
        if header_size != protocol.FRAME_HEADER_BYTES:
            raise RuntimeError(f"Unexpected header size: {header_size}")

        sdiv_idx = (
            time_config & protocol.TIME_CONFIG_SDIV_MASK
        ) >> protocol.TIME_CONFIG_SDIV_POS
        if sdiv_idx < len(protocol.S_DIV_OPTIONS_S):
            s_div = protocol.S_DIV_OPTIONS_S[sdiv_idx]
        else:
            s_div = protocol.S_DIV_OPTIONS_S[-1]
        span_raw = int(
            (reserved & protocol.FRAME_META_SPAN_MASK) >> protocol.FRAME_META_SPAN_POS
        )
        if span_raw > 0:
            sample_rate_hz = max(1, int(round(span_raw / max(10.0 * s_div, 1e-9))))
        else:
            sample_rate_hz = max(
                1, int(round(sample_count / max(10.0 * s_div, 1e-9)))
            )
        # Firmware trigger byte is 8-bit, where one LSB equals 16 ADC codes.
        trigger_adc_code = min(int(trigger) * 16, 4095)
        trigger_level_v = self._adc_to_volts(trigger_adc_code)
        trigger_found = (reserved & protocol.FRAME_META_TRIGGER_FOUND_BIT) != 0
        trigger_source = (
            "CH2"
            if (reserved & protocol.FRAME_META_TRIGGER_SRC_CH2_BIT) != 0
            else "CH1"
        )
        trigger_index_raw = int(reserved & protocol.FRAME_META_TRIGGER_INDEX_MASK)
        trigger_index = (
            trigger_index_raw
            if (trigger_found and 0 <= trigger_index_raw < int(sample_count))
            else None
        )

        ch1_enabled = (ch_config & protocol.CH_CONFIG_CH1_EN) != 0
        ch2_enabled = (ch_config & protocol.CH_CONFIG_CH2_EN) != 0
        ch1_vdiv_idx = (
            ch_config & protocol.CH_CONFIG_CH1_VDIV_MASK
        ) >> protocol.CH_CONFIG_CH1_VDIV_POS
        ch2_vdiv_idx = (
            ch_config & protocol.CH_CONFIG_CH2_VDIV_MASK
        ) >> protocol.CH_CONFIG_CH2_VDIV_POS
        ch1_v_div = (
            protocol.V_DIV_OPTIONS_V[ch1_vdiv_idx]
            if ch1_vdiv_idx < len(protocol.V_DIV_OPTIONS_V)
            else 1.0
        )
        ch2_v_div = (
            protocol.V_DIV_OPTIONS_V[ch2_vdiv_idx]
            if ch2_vdiv_idx < len(protocol.V_DIV_OPTIONS_V)
            else 1.0
        )

        ch1: list[float]
        ch2: list[float]
        if _np is not None:
            raw = _np.frombuffer(payload, dtype="<u2")
            expected = int(sample_count) * 2
            if raw.size < expected:
                raise RuntimeError("Frame payload truncated")
            raw = raw[:expected].reshape(int(sample_count), 2)
            volts = (raw.astype(_np.float32) * (3.3 / 4095.0)) - 1.65
            ch1 = volts[:, 0].tolist()
            ch2 = volts[:, 1].tolist()
        else:
            ch1 = [0.0] * int(sample_count)
            ch2 = [0.0] * int(sample_count)
            for i in range(sample_count):
                o = i * protocol.PAYLOAD_BYTES_PER_SAMPLE
                raw1 = payload[o] | (payload[o + 1] << 8)
                raw2 = payload[o + 2] | (payload[o + 3] << 8)
                ch1[i] = self._adc_to_volts(raw1)
                ch2[i] = self._adc_to_volts(raw2)

        return ScopeFrame(
            sample_rate_hz=sample_rate_hz,
            ch1=ch1,
            ch2=ch2,
            trigger_index=trigger_index,
            trigger_found=trigger_found,
            trigger_source=trigger_source,
            ch1_enabled=ch1_enabled,
            ch2_enabled=ch2_enabled,
            ch1_v_div=ch1_v_div,
            ch2_v_div=ch2_v_div,
            s_div=s_div,
            trigger_level_v=trigger_level_v,
        )

    def _try_extract_one_packet(self) -> tuple[bytes, bytes] | None:
        if len(self._rx) < protocol.FRAME_HDR_SIZE:
            return None

        magic_at = self._rx.find(protocol.MAGIC)
        if magic_at < 0:
            # Keep one tail byte for partial magic match.
            del self._rx[:-1]
            return None
        if magic_at > 0:
            del self._rx[:magic_at]

        if len(self._rx) < protocol.FRAME_HDR_SIZE:
            return None

        header = bytes(self._rx[: protocol.FRAME_HDR_SIZE])
        magic, header_size, sample_count, _, _, _, _ = struct.unpack(
            protocol.FRAME_HDR_FMT, header
        )
        if (
            magic != protocol.MAGIC_U16
            or header_size != protocol.FRAME_HEADER_BYTES
            or sample_count == 0
        ):
            del self._rx[0]
            return None

        payload_len = int(sample_count) * protocol.PAYLOAD_BYTES_PER_SAMPLE
        if payload_len > 64_000:
            del self._rx[0]
            return None

        total_len = protocol.FRAME_HDR_SIZE + payload_len
        if len(self._rx) < total_len:
            return None

        payload = bytes(self._rx[protocol.FRAME_HDR_SIZE : total_len])
        del self._rx[:total_len]
        return header, payload

    def _ingest_available(self) -> None:
        if self._serial is None:
            raise RuntimeError("USB serial transport is unavailable")
        # Drain all currently available serial data so each UI tick can jump
        # directly to the newest complete frame.
        while True:
            chunk = self._serial.read(65536)
            if not chunk:
                break
            self._rx.extend(chunk)
        # Guard against unbounded backlog.
        if len(self._rx) > 512_000:
            del self._rx[:-128_000]

    @staticmethod
    def _adc_to_volts(code: int) -> float:
        # Convert 12-bit ADC code to bipolar volts centered around midscale.
        return ((float(code) / 4095.0) * 3.3) - 1.65
