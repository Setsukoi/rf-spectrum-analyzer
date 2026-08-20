"""Named instrument setups for repeatable checks.

A preset is only data. ``configure_kwargs`` is what ``N9020A.configure``
accepts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str
    center_hz: float
    span_hz: float
    rbw_hz: float
    attenuation_db: float
    points: int
    detector: str = "RMS"

    @property
    def configure_kwargs(self) -> dict:
        return {
            "center_hz": self.center_hz,
            "span_hz": self.span_hz,
            "rbw_hz": self.rbw_hz,
            "attenuation_db": self.attenuation_db,
            "points": self.points,
            "detector": self.detector,
        }


FREQUENCY_PRESETS: tuple[Preset, ...] = (
    Preset(
        name="1 GHz",
        center_hz=1e9,
        span_hz=10e6,
        rbw_hz=30e3,
        attenuation_db=10.0,
        points=1001,
        detector="RMS",
    ),
)


def preset(name: str) -> Preset:
    """Look up a preset by ``name``. Raises ``KeyError`` if it is missing."""
    for item in FREQUENCY_PRESETS:
        if item.name == name:
            return item
    known = ", ".join(item.name for item in FREQUENCY_PRESETS)
    raise KeyError(f"unknown preset {name!r}; known: {known}")
