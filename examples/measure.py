from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from rfsa import N9020A, Storage, connect

def open_analyzer(address: str, *, fake: bool) -> N9020A:
    if fake:
        from rfsa.fake import FakeResource, tone_trace
        print("[fake] no instrument — amplitudes below are invented\n")
        return N9020A(FakeResource(trace=tone_trace(peak_dbm=-20.4)))
    return connect(address)

def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def run_sweep(sa: N9020A, db: Storage, *, center_hz: float, span_hz: float,
              rbw_hz: float, attenuation_db: float, points: int,
              operator: str | None) -> int:
    run = db.start_run("1 GHz tone check", identity=sa.identity, operator=operator)
    settings = sa.configure(center_hz=center_hz, span_hz=span_hz, rbw_hz=rbw_hz,
                            ref_level_dbm=0, attenuation_db=attenuation_db,
                            points=points, detector="RMS")
    print(f"RBW asked {rbw_hz / 1e3:g} kHz -> analyzer used "
          f"{settings.rbw_hz / 1e3:g} kHz, sweep {settings.sweep_time_s * 1e3:.1f} ms")

    sweep = sa.capture(label=f"{center_hz / 1e6:.3f} MHz tone")
    peak = sweep.peak
    print(peak)

    sweep_id = db.save_sweep(run, sweep)
    db.finish_run(run)

    for row in db.peaks(run):
        print(f"#{row['id']} {row['label']}: "
              f"{row['peak_dbm']:.2f} dBm @ {row['peak_hz'] / 1e6:.4f} MHz")
    return sweep_id


def run_frequency_check(sa: N9020A, db: Storage, *, center_hz: float, span_hz: float,
                        rbw_hz: float, attenuation_db: float, points: int,
                        operator: str | None, screenshot: Path) -> tuple[int, Path]:
    run = db.start_run("frequency check", identity=sa.identity, operator=operator)
    settings = sa.configure(center_hz=center_hz, span_hz=span_hz, rbw_hz=rbw_hz,
                            attenuation_db=attenuation_db, points=points,
                            detector="RMS")
    sweep = sa.capture(label=f"{center_hz / 1e6:.3f} MHz frequency check")

    peak = sa.peak_search()
    frequency = sa.marker_frequency_counter()
    error_hz = frequency.value - center_hz

    image = sa.save_screen_image(screenshot)
    sweep_id = db.save_sweep(run, sweep, counter_hz=frequency.value,
                             frequency_error_hz=error_hz)
    db.finish_run(run)

    print(f"center: {settings.center_hz:.3f} Hz, span: {settings.span_hz:.3f} Hz")
    print(f"peak marker: {peak.value:.2f} dBm @ {peak.frequency_hz:.3f} Hz")
    print(f"counter: {frequency.value:.3f} Hz")
    print(f"error: {error_hz:+.3f} Hz")
    return sweep_id, image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("address", nargs="?", default="",
                        help="VISA resource, e.g. TCPIP0::192.168.10.2::inst0::INSTR")
    parser.add_argument("--fake", action="store_true",
                        help="run against the fake analyzer (no hardware)")
    parser.add_argument("--frequency", action="store_true",
                        help="frequency check: counter + screenshot")
    parser.add_argument("--db", default="measurements.db",
                        help="SQLite database path")
    parser.add_argument("--center-hz", type=float, default=1e9)
    parser.add_argument("--span-hz", type=float, default=10e6)
    parser.add_argument("--rbw-hz", type=float, default=30e3)
    parser.add_argument("--attenuation-db", type=float, default=10.0)
    parser.add_argument("--points", type=int, default=1001)
    parser.add_argument("--operator", default=None)
    parser.add_argument("--screenshot", default=None,
                        help="PNG path for --frequency (default: screenshots/frequency_<time>.png)")
    args = parser.parse_args()

    if not args.fake and not args.address:
        parser.error("give a VISA address, or use --fake")

    shared = dict(center_hz=args.center_hz, span_hz=args.span_hz,
                  rbw_hz=args.rbw_hz, attenuation_db=args.attenuation_db,
                  points=args.points, operator=args.operator)

    with open_analyzer(args.address, fake=args.fake) as sa, Storage(args.db) as db:
        print(sa.identity)
        if args.frequency:
            screenshot = Path(args.screenshot or f"screenshots/frequency_{timestamp()}.png")
            sweep_id, image = run_frequency_check(sa, db, screenshot=screenshot, **shared)
            print(f"sweep #{sweep_id} saved to {args.db}")
            print(f"screenshot saved to {image}")
        else:
            sweep_id = run_sweep(sa, db, **shared)
            print(f"saved sweep {sweep_id} to {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
