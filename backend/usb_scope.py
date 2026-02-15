from __future__ import annotations

from backend.scope_source import ScopeFrame


class UsbScope:
    """
    Real USB transport stub.

    This class is intentionally shaped the same as MockScope so the UI can switch
    sources without changing plotting or control logic.
    """

    def __init__(self) -> None:
        self._connected = False

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
        # TODO: enumerate and open actual device.
        self._connected = True

    def disconnect(self) -> None:
        """Release USB resources."""
        self._connected = False

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

        # We raise for now so UI can show "not implemented" clearly.
        raise NotImplementedError("USB scope backend is not implemented yet")
