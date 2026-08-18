from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rfsa.web.app import create_app


def make_app(tmp_path):
    return create_app(
        db_path=str(tmp_path / "m.db"),
        screenshot_dir=str(tmp_path / "screenshots"),
    )


def test_page_is_served(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "扫描" in response.text
        assert "应用参数" in response.text


def test_scan_without_connection_is_rejected(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post("/api/scan", json={})
        assert response.status_code == 409
        assert "尚未连接" in response.json()["detail"]


def test_fake_configure_scan_and_history(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        connected = client.post("/api/connect", json={"fake": True})
        assert connected.status_code == 200
        assert connected.json()["connected"] is True
        assert connected.json()["fake"] is True

        configured = client.post("/api/configure", json={
            "center_hz": 1e9,
            "span_hz": 10e6,
            "rbw_hz": 30e3,
            "attenuation_db": 10,
            "points": 1001,
            "detector": "RMS",
        })
        assert configured.status_code == 200
        assert configured.json()["center_hz"] == 1e9
        assert configured.json()["detector"] == "RMS"

        scanned = client.post("/api/scan", json={
            "center_hz": 1e9,
            "span_hz": 10e6,
            "rbw_hz": 30e3,
            "attenuation_db": 10,
            "points": 1001,
            "detector": "RMS",
        })
        assert scanned.status_code == 200
        body = scanned.json()
        assert body["id"] == 1
        assert len(body["frequencies_hz"]) == 1001
        assert len(body["amplitudes_dbm"]) == 1001
        assert body["counter_hz"] == pytest.approx(1000000123.456)
        assert body["frequency_error_hz"] == pytest.approx(123.456)
        assert body["peak_dbm"] == -20.5
        assert body["applied"] is True
        assert body["has_screenshot"] is True
        shot = Path(body["screenshot_path"])
        assert shot.is_file()
        assert shot.read_bytes().startswith(b"\x89PNG")

        listing = client.get("/api/sweeps")
        assert listing.status_code == 200
        assert len(listing.json()) == 1
        assert listing.json()[0]["id"] == 1
        assert listing.json()[0]["center_hz"] == 1e9
        assert listing.json()[0]["has_screenshot"] is True

        loaded = client.get("/api/sweeps/1")
        assert loaded.status_code == 200
        assert loaded.json()["amplitudes_dbm"] == body["amplitudes_dbm"]

        png = client.get("/api/sweeps/1/screenshot")
        assert png.status_code == 200
        assert png.content.startswith(b"\x89PNG")

        missing = client.get("/api/sweeps/99")
        assert missing.status_code == 404


def test_bad_parameter_stays_in_chinese_wrapper(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        client.post("/api/connect", json={"fake": True})
        response = client.post("/api/configure", json={"center_hz": 99e9})
        assert response.status_code == 400
        assert response.json()["detail"].startswith("参数错误")


def test_bad_parameter_on_scan_stops_before_saving(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        client.post("/api/connect", json={"fake": True})
        response = client.post("/api/scan", json={"center_hz": 99e9})
        assert response.status_code == 400
        assert response.json()["detail"].startswith("参数错误")
        assert client.get("/api/sweeps").json() == []


def test_scan_saves_when_screenshot_fails(tmp_path, monkeypatch):
    from rfsa.analyzer import N9020A
    from rfsa.errors import InstrumentError

    def fail_shot(self, path):
        raise InstrumentError("hcopy failed")

    monkeypatch.setattr(N9020A, "save_screen_image", fail_shot)
    with TestClient(make_app(tmp_path)) as client:
        client.post("/api/connect", json={"fake": True})
        scanned = client.post("/api/scan", json={
            "center_hz": 1e9,
            "span_hz": 10e6,
            "points": 1001,
        })
        assert scanned.status_code == 200
        body = scanned.json()
        assert body["has_screenshot"] is False
        assert "hcopy failed" in body["screenshot_error"]
        listing = client.get("/api/sweeps").json()
        assert len(listing) == 1
        assert listing[0]["has_screenshot"] is False


def test_scan_resumes_continuous_sweep_after_screenshot(tmp_path):
    from rfsa.analyzer import N9020A
    from rfsa.fake import FakeResource, tone_trace
    from rfsa.web.session import Lab

    resource = FakeResource(trace=tone_trace(peak_dbm=-20.4))
    lab = Lab(str(tmp_path / "m.db"), screenshot_dir=tmp_path / "screenshots")
    try:
        lab.sa = N9020A(resource)
        lab.fake = True
        lab.scan()
        off = resource.writes.index(":INITiate:CONTinuous 0")
        store = next(i for i, cmd in enumerate(resource.writes)
                     if cmd.startswith(":MMEM:STOR:SCR"))
        on = resource.writes.index(":INITiate:CONTinuous 1")
        assert off < store < on
    finally:
        lab.close()


def test_scan_saves_when_the_frequency_counter_has_nothing_to_count(tmp_path):
    """A silent tone must not cost us the sweep, nor store 9.91e37 as a result."""
    with TestClient(make_app(tmp_path)) as client:
        client.post("/api/connect", json={"fake": True})
        lab = client.app.state.lab
        lab.sa._res.responses[":CALCulate:MARKer1:FCOunt:X?"] = "9.91e37"

        scanned = client.post("/api/scan", json={"center_hz": 1e9, "span_hz": 10e6})
        assert scanned.status_code == 200
        body = scanned.json()
        assert body["counter_hz"] is None
        assert body["frequency_error_hz"] is None
        assert "9.91e37" in body["counter_error"]
        assert body["peak_dbm"] == -20.5, "the sweep itself is still recorded"
        assert len(client.get("/api/sweeps").json()) == 1


def test_clear_history_empties_listing_and_files(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        client.post("/api/connect", json={"fake": True})
        scanned = client.post("/api/scan", json={
            "center_hz": 1e9,
            "span_hz": 10e6,
            "points": 1001,
        })
        shot = Path(scanned.json()["screenshot_path"])
        assert shot.is_file()
        cleared = client.post("/api/history/clear")
        assert cleared.status_code == 200
        assert client.get("/api/sweeps").json() == []
        assert not shot.is_file()
