"""Repeatable measurement procedures built from presets."""

from __future__ import annotations

from pathlib import Path

from .analyzer import N9020A
from .errors import RfsaError
from .models import Reading, Sweep
from .presets import Preset
from .storage import Storage


def read_then_hold(
    sa: N9020A, *, label: str | None = None,
) -> tuple[Sweep, Reading, float | None, float | None, str | None]:
    """Read peak, frequency counter and trace while sweeping, then freeze.

    The marker counter only counts during a live sweep. Stopping first is
    what blanks Cnt1 on the screen. Screenshot the held display afterwards.
    """
    sa.continuous_sweep()
    sa.wait_sweep()
    peak = sa.peak_search()
    counter_hz = None
    error_hz = None
    counter_error = None
    try:
        counter_hz = sa.marker_frequency_counter().value
    except RfsaError as exc:
        counter_error = str(exc)
    sweep = sa.read_trace(label=label)
    if counter_hz is not None:
        error_hz = counter_hz - sweep.settings.center_hz
    sa.hold()
    return sweep, peak, counter_hz, error_hz, counter_error


def run_frequency_check(sa: N9020A, db: Storage, preset: Preset,
                        screenshot_path: str | Path) -> tuple[int, Path]:
    run = db.start_run(identity=sa.identity)
    try:
        sa.configure(**preset.configure_kwargs)
        sweep, peak, counter_hz, error_hz, _error = read_then_hold(
            sa, label=f"{preset.name} frequency check")
        image = sa.save_screen_image(screenshot_path)
        sweep_id = db.save_sweep(
            run, sweep,
            peak_hz=peak.frequency_hz, peak_dbm=peak.value,
            counter_hz=counter_hz, frequency_error_hz=error_hz,
            screenshot_path=str(image.resolve()))
        return sweep_id, image
    finally:
        db.finish_run(run)
        sa.continuous_sweep()
