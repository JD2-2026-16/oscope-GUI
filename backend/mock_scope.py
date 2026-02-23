from __future__ import annotations

import math

from backend.scope_source import ScopeFrame


class MockScope:
    """
    Software-only scope generator for UI bring-up.

    This class models what your firmware/USB layer should eventually provide:
    one frame at a time, with both channels sampled simultaneously.
    """

    def __init__(self, sample_rate_hz: int = 250_000, frame_samples: int = 4096) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.frame_samples = frame_samples
        self._connected = False

        # Phase accumulators let each frame continue where the last one ended,
        # so the waveform "moves" naturally instead of resetting every frame.
        self._phase1 = 0.0
        self._phase2 = 0.0

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def get_next_frame(self) -> ScopeFrame:
        if not self._connected:
            raise RuntimeError("MockScope is not connected")

        # Synthetic signal setup:
        # - CH1 is cleaner and lower-frequency.
        # - CH2 is a mix of tones to imitate a noisier channel.
        f1 = 12_500.0
        f2 = 31_000.0
        f2b = 4_500.0

        step1 = 2.0 * math.pi * f1 / self.sample_rate_hz
        step2 = 2.0 * math.pi * f2 / self.sample_rate_hz
        ch1: list[float] = []
        ch2: list[float] = []

        for _ in range(self.frame_samples):
            # Volt units are arbitrary here; GUI scaling uses V/div controls.
            v1 = 1.1 * math.sin(self._phase1) + 0.08 * math.sin(self._phase1 * 6.0)
            v2 = 0.65 * math.sin(self._phase2) + 0.35 * math.sin(self._phase2 * (f2b / f2))

            ch1.append(v1)
            ch2.append(v2)

            self._phase1 += step1
            self._phase2 += step2

        # Keep phase numbers bounded so they never grow to huge floats over time.
        self._phase1 = self._phase1 % (2.0 * math.pi)
        self._phase2 = self._phase2 % (2.0 * math.pi)

        # Trigger index is optional metadata from backend/firmware.
        # For mock mode we leave it None and let UI find a crossing itself.
        return ScopeFrame(sample_rate_hz=self.sample_rate_hz, ch1=ch1, ch2=ch2, trigger_index=None)
