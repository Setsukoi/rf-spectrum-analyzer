"""Storage tests."""

from dataclasses import fields

import numpy as np
import pytest

from rfsa import Identity, N9020A, Settings, Storage, Sweep
from rfsa.fake import FakeResource


def make_sweep(label="tone", peak_at=500, peak_dbm=-20.0, points=1001) -> Sweep:
    amplitudes = np.full(points, -95.0)
    amplitudes[peak_at] = peak_dbm
    settings = Settings(center_hz=1e9, span_hz=10e6, rbw_hz=30e3, vbw_hz=30e3,
                        points=points, sweep_time_s=0.022, ref_level_dbm=-10.0,
                        attenuation_db=10.0, preamp=False, detector="NORM",
                        trace_type="WRIT")
    return Sweep(amplitudes_dbm=amplitudes, settings=settings, label=label)


class TestSchema:
    def test_tables_exist(self, db):
        names = {r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"runs", "sweeps"} <= names

    def test_every_settings_field_has_a_column(self, db):
        columns = {r["name"] for r in db.query("PRAGMA table_info(sweeps)")}
        assert {f.name for f in fields(Settings)} <= columns

    def test_screenshot_path_column_exists(self, db):
        columns = {r["name"] for r in db.query("PRAGMA table_info(sweeps)")}
        assert "screenshot_path" in columns

    def test_deleting_a_run_removes_its_sweeps(self, db):
        run = db.start_run("temp")
        db.save_sweep(run, make_sweep())
        db.connection.execute("DELETE FROM runs WHERE id = ?", (run,))
        db.connection.commit()
        assert db.query("SELECT * FROM sweeps") == []


class TestRoundTrip:
    def test_a_sweep_comes_back_bit_for_bit(self, db):
        run = db.start_run("round trip")
        original = make_sweep()
        loaded = db.load_sweep(db.save_sweep(run, original))
        assert np.array_equal(loaded.amplitudes_dbm, original.amplitudes_dbm)
        assert loaded.settings == original.settings

    def test_frequency_counter_columns(self, db):
        run = db.start_run()
        db.save_sweep(run, make_sweep(), counter_hz=1e9 + 123.456,
                      frequency_error_hz=123.456)
        row = db.query("SELECT counter_hz, frequency_error_hz FROM sweeps")[0]
        assert row["counter_hz"] == pytest.approx(1e9 + 123.456)
        assert row["frequency_error_hz"] == pytest.approx(123.456)

    def test_counter_frequency_can_override_peak_hz(self, db):
        run = db.start_run()
        db.save_sweep(run, make_sweep(peak_dbm=-12.5), peak_hz=1e9 + 123.456,
                      peak_dbm=-12.5, counter_hz=1e9 + 123.456)
        row = db.query("SELECT peak_hz, peak_dbm, counter_hz FROM sweeps")[0]
        assert row["peak_dbm"] == pytest.approx(-12.5)
        assert row["peak_hz"] == pytest.approx(1e9 + 123.456)
        assert row["counter_hz"] == pytest.approx(1e9 + 123.456)

    def test_missing_sweep_raises(self, db):
        with pytest.raises(KeyError):
            db.load_sweep(999)

    def test_screenshot_path_is_stored(self, db):
        run = db.start_run()
        db.save_sweep(run, make_sweep(), screenshot_path="screenshots/scan.png")
        assert db.load_sweep_row(1)["screenshot_path"] == "screenshots/scan.png"


class TestQuerying:
    def test_peaks_across_a_run(self, db):
        run = db.start_run("sweep over level")
        for level in (-20.0, -25.0, -30.0):
            db.save_sweep(run, make_sweep(label=f"{level} dBm", peak_dbm=level))
        rows = db.peaks(run)
        assert [r["peak_dbm"] for r in rows] == [-20.0, -25.0, -30.0]

    def test_list_sweeps_newest_first(self, db):
        run = db.start_run("history")
        first = db.save_sweep(run, make_sweep(label="first", peak_dbm=-10.0))
        second = db.save_sweep(run, make_sweep(label="second", peak_dbm=-20.0),
                               counter_hz=1e9 + 3.0, frequency_error_hz=3.0)
        rows = db.list_sweeps()
        assert [r["id"] for r in rows] == [second, first]
        assert rows[0]["label"] == "second"
        assert rows[0]["counter_hz"] == pytest.approx(1e9 + 3.0)
        assert rows[0]["title"] == "history"

    def test_run_metadata_records_the_instrument(self, db):
        identity = Identity("Agilent Technologies", "N9020A", "MY49010001", "A.14.16")
        run = db.start_run("frequency check", identity=identity, operator="setsukoi")
        db.finish_run(run)
        row = db.query("SELECT * FROM runs WHERE id = ?", (run,))[0]
        assert (row["model"], row["serial"], row["operator"]) == \
            ("N9020A", "MY49010001", "setsukoi")


def test_capture_to_database_end_to_end(db):
    resource = FakeResource()
    resource.trace = np.full(1001, -95.0)
    resource.trace[500] = -20.0
    analyzer = N9020A(resource)

    run = db.start_run("end to end", identity=analyzer.identity)
    sweep = analyzer.capture(label="1 GHz tone")
    sweep_id = db.save_sweep(run, sweep)
    db.finish_run(run)

    assert db.load_sweep(sweep_id).peak.value == pytest.approx(-20.0)
