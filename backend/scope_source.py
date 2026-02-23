from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class ScopeFrame:
    """
    One frame from the scope firmware.

    The GUI treats acquisition as frame-based (not byte-stream-based). That matches
    your USB FS bandwidth constraints and keeps UI timing simple: each timer tick
    requests one frame and renders it.
    """

    sample_rate_hz: int
    ch1: list[float]
    ch2: list[float]
    trigger_index: int | None = None
    ch1_enabled: bool | None = None
    ch2_enabled: bool | None = None
    ch1_v_div: float | None = None
    ch2_v_div: float | None = None
    s_div: float | None = None

    @property
    def sample_count(self) -> int:
        """Number of samples per channel in this frame."""
        return min(len(self.ch1), len(self.ch2))


class ScopeSource(Protocol):
    """
    Interface for any data source (real USB device or mock generator).

    Keeping this tiny and explicit makes it easy to swap backends while keeping the
    rest of the GUI unchanged.
    """

    def connect(self) -> None:
        """Open underlying transport/resources."""

    def disconnect(self) -> None:
        """Close transport/resources."""

    def get_next_frame(self) -> ScopeFrame:
        """Fetch one complete frame suitable for immediate plotting."""
