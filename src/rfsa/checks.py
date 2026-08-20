"""Repeatable measurement procedures built from presets."""

from __future__ import annotations

from pathlib import Path

from .analyzer import N9020A
from .errors import RfsaError
from .presets import Preset
from .storage import Storage


def run_frequency_check(sa: N9020A, db: Storage, preset: Preset,
                        screenshot_path: str | Path) -> tuple[int, Path]:
    run = db.start_run(identity=sa.identity)
    try:
        settings = sa.configure(**preset.configure_kwargs)
        sweep = sa.capture(label=f"{preset.name} frequency check")
        peak = sa.peak_search()
        counter_hz = None
        error_hz = None
        try:
            frequency = sa.marker_frequency_counter()
            counter_hz = frequency.value
            error_hz = counter_hz - settings.center_hz
        except RfsaError:
            pass
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
