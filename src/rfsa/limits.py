"""Instrument limits for the lab N9020A (526 frequency option, P26 preamp).

Values from the N9020A data sheet (5989-4942EN). There is only one analyzer
in the lab, so no option detection or per-instrument querying.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    """What the hardware will accept. All frequencies in Hz, powers in dBm."""

    freq_min_hz: float = 10.0
    freq_max_hz: float = 26.5e9
    span_min_hz: float = 10.0

    rbw_min_hz: float = 1.0
    rbw_max_hz: float = 8e6
    vbw_min_hz: float = 1.0
    vbw_max_hz: float = 50e6

    points_min: int = 1
    points_max: int = 40001
    sweep_time_min_s: float = 1e-3
    sweep_time_max_s: float = 4000.0
    zero_span_time_min_s: float = 1e-6
    zero_span_time_max_s: float = 6000.0

    ref_level_min_dbm: float = -170.0
    ref_level_max_dbm: float = 30.0
    atten_min_db: float = 0.0
    atten_max_db: float = 70.0
    atten_step_db: float = 2.0

    has_preamp: bool = True
    markers: int = 12
    traces: int = 6

    def sweep_time_range(self, span_hz: float) -> tuple[float, float]:
        """Sweep time limits depend on whether the analyzer is in zero span."""
        if span_hz == 0:
            return self.zero_span_time_min_s, self.zero_span_time_max_s
        return self.sweep_time_min_s, self.sweep_time_max_s
