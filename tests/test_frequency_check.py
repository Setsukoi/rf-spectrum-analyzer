"""Full frequency-check procedure against the fake analyzer."""

import pytest

from rfsa import N9020A, Storage
from rfsa.checks import run_frequency_check
from rfsa.fake import FakeResource, tone_trace
from rfsa.presets import FREQUENCY_PRESETS, preset


def test_preset_lookup():
    assert preset("1 GHz").center_hz == 1e9


def test_frequency_check_on_fake_analyzer(tmp_path):
    resource = FakeResource(trace=tone_trace(peak_dbm=-20.4))
    sa = N9020A(resource)
    chosen = preset("1 GHz")
    shot = tmp_path / "1ghz.png"
    with Storage(":memory:") as db:
        sweep_id, image = run_frequency_check(sa, db, chosen, shot)

        assert ":SENSe:FREQuency:CENTer 1000000000.000" in resource.writes
        assert ":SENSe:FREQuency:SPAN 10000000.000" in resource.writes
        assert ":INITiate:CONTinuous 0" in resource.writes
        assert ":INITiate:IMMediate" in resource.writes
        assert ":CALCulate:MARKer1:MAXimum" in resource.writes
        assert ":CALCulate:MARKer1:FCOunt:STATe 1" in resource.writes
        assert any(cmd.startswith(":MMEM:STOR:SCR") for cmd in resource.writes)
        assert resource.writes[-1] == ":INITiate:CONTinuous 1"

        assert image == shot
        assert image.read_bytes().startswith(b"\x89PNG")

        row = db.load_sweep_row(sweep_id)
        assert row["center_hz"] == 1e9
        assert row["span_hz"] == 10e6
        assert row["rbw_hz"] == 30e3
        assert row["label"] == "1 GHz frequency check"
        assert row["counter_hz"] == pytest.approx(1000000123.456)
        assert row["frequency_error_hz"] == pytest.approx(123.456)
        assert row["screenshot_path"] == str(image.resolve())
        finished = db.query("SELECT finished_at FROM runs WHERE id = ?",
                            (row["run_id"],))[0]["finished_at"]
        assert finished is not None


def test_each_preset_completes(tmp_path):
    for chosen in FREQUENCY_PRESETS:
        resource = FakeResource(trace=tone_trace(peak_dbm=-20.4))
        sa = N9020A(resource)
        shot = tmp_path / f"{chosen.name}.png"
        with Storage(":memory:") as db:
            sweep_id, image = run_frequency_check(sa, db, chosen, shot)
            assert sweep_id >= 1
            assert image.read_bytes().startswith(b"\x89PNG")


def test_frequency_check_saves_when_the_counter_has_no_signal(tmp_path):
    resource = FakeResource(trace=tone_trace(peak_dbm=-20.4))
    resource.responses[":CALCulate:MARKer1:FCOunt:X?"] = "9.91e37"
    sa = N9020A(resource)
    shot = tmp_path / "nosignal.png"
    with Storage(":memory:") as db:
        sweep_id, image = run_frequency_check(sa, db, preset("1 GHz"), shot)
        row = db.load_sweep_row(sweep_id)
        assert row["counter_hz"] is None
        assert row["frequency_error_hz"] is None
        assert image.read_bytes().startswith(b"\x89PNG")
