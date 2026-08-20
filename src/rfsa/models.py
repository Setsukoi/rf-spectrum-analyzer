"""Value objects passed between the driver, the storage layer and your tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

DETECTORS = ("NORM", "AVER", "POS", "SAMP", "NEG", "RMS", "QPE")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Identity:
    vendor: str
    model: str
    serial: str
    firmware: str

    @classmethod
    def parse(cls, idn: str) -> "Identity":
        """``*IDN?`` -> ``Agilent Technologies,N9020A,MY49010001,A.14.16``."""
        parts = [p.strip() for p in idn.split(",")] + ["", "", "", ""]
        return cls(*parts[:4])

    def __str__(self) -> str:
        return f"{self.vendor} {self.model} SN:{self.serial} FW:{self.firmware}"


@dataclass(frozen=True)
class Settings:
    """The analyzer state a sweep was taken with.

    Always read back from the instrument rather than remembered from what was
    written: auto-coupled parameters (RBW, VBW, sweep time, attenuation) move
    on their own, and RBW snaps to the nearest available filter.
    """

    center_hz: float
    span_hz: float
    rbw_hz: float
    vbw_hz: float
    points: int
    sweep_time_s: float
    ref_level_dbm: float
    attenuation_db: float
    preamp: bool
    detector: str
    trace_type: str

    @property
    def start_hz(self) -> float:
        return self.center_hz - self.span_hz / 2

    @property
    def stop_hz(self) -> float:
        return self.center_hz + self.span_hz / 2


@dataclass(frozen=True)
class Reading:
    """One scalar result — a marker, a channel power, anything worth querying
    later without unpacking a whole trace."""

    name: str
    value: float
    unit: str = "dBm"
    frequency_hz: float | None = None

    @classmethod
    def at_frequency(cls, requested_hz: float, value: float, unit: str = "dBm",
                     *, frequency_hz: float | None = None) -> "Reading":
        """A reading named after the frequency that was *asked* for."""
        return cls(f"{requested_hz / 1e6:.3f} MHz", value, unit,
                   requested_hz if frequency_hz is None else frequency_hz)

    def __str__(self) -> str:
        where = f" @ {self.frequency_hz / 1e6:.6f} MHz" if self.frequency_hz else ""
        return f"{self.name}: {self.value:.3f} {self.unit}{where}"


@dataclass(frozen=True)
class Sweep:
    """One captured trace plus the settings it was captured with."""

    amplitudes_dbm: np.ndarray
    settings: Settings
    trace: int = 1
    captured_at: datetime = field(default_factory=utcnow)
    label: str | None = None
    _frequency_axis_hz: np.ndarray | None = field(default=None, repr=False, compare=False)

    def __len__(self) -> int:
        return len(self.amplitudes_dbm)

    @property
    def frequencies_hz(self) -> np.ndarray:
        if self._frequency_axis_hz is not None:
            return self._frequency_axis_hz
        s = self.settings
        if s.span_hz == 0:
            return np.full(len(self), s.center_hz)
        return np.linspace(s.start_hz, s.stop_hz, len(self))

    @property
    def peak(self) -> Reading:
        return self._reading_at(int(np.argmax(self.amplitudes_dbm)), "peak")

    def at(self, frequency_hz: float) -> Reading:
        """The trace point nearest a frequency."""
        axis = self.frequencies_hz
        index = int(np.argmin(np.abs(axis - frequency_hz)))
        return Reading.at_frequency(frequency_hz, float(self.amplitudes_dbm[index]),
                                    frequency_hz=float(axis[index]))

    def _reading_at(self, index: int, name: str) -> Reading:
        return Reading(name, float(self.amplitudes_dbm[index]), "dBm",
                       float(self.frequencies_hz[index]))
