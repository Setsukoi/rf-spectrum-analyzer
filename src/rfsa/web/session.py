"""One analyzer + one database, shared by the web process."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from rfsa import N9020A, Storage, connect
from rfsa.checks import read_then_hold
from rfsa.errors import RfsaError
from rfsa.models import Identity, Settings, Sweep

_CONFIGURE_KEYS = (
    "center_hz", "span_hz", "rbw_hz", "vbw_hz", "points",
    "sweep_time_s", "ref_level_dbm", "attenuation_db", "preamp", "detector",
)


class NotConnected(RfsaError):
    """No analyzer session is open."""


class Lab:
    def __init__(self, db_path: str = "measurements.db",
                 screenshot_dir: str | Path = "screenshots"):
        self.db = Storage(db_path)
        self.screenshot_dir = Path(screenshot_dir)
        self._lock = threading.Lock()
        self.sa: N9020A | None = None
        self.run_id: int | None = None
        self.fake = False

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()
        self.db.close()

    def _close_unlocked(self) -> None:
        if self.run_id is not None:
            self.db.finish_run(self.run_id)
            self.run_id = None
        if self.sa is not None:
            self.sa.close()
            self.sa = None
        self.fake = False

    def _require(self) -> N9020A:
        if self.sa is None:
            raise NotConnected("尚未连接仪器")
        return self.sa

    def connect(self, address: str = "", *, fake: bool = False) -> dict[str, Any]:
        if not fake and not address.strip():
            raise NotConnected("请填写 VISA 地址，或使用测试数据")
        with self._lock:
            self._close_unlocked()
            if fake:
                from rfsa.fake import fake_resource
                self.sa = N9020A(fake_resource())
                self.fake = True
            else:
                self.sa = connect(address.strip())
                self.fake = False
            self.sa.prepare()
            self.run_id = self.db.start_run(identity=self.sa.identity)
            return self._status_unlocked()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._close_unlocked()
            return self._status_unlocked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def _status_unlocked(self) -> dict[str, Any]:
        sa = self.sa
        return {
            "connected": sa is not None,
            "fake": self.fake,
            "run_id": self.run_id,
            "identity": None if sa is None else _identity_json(sa.identity),
            "settings": None if sa is None else _settings_json(sa.settings()),
        }

    def configure(self, fields: dict[str, Any]) -> dict[str, Any]:
        kwargs = {key: fields[key] for key in _CONFIGURE_KEYS
                  if key in fields and fields[key] is not None}
        with self._lock:
            sa = self._require()
            settings = sa.configure(**kwargs) if kwargs else sa.settings()
            return _settings_json(settings)

    def scan(self, label: str | None = None,
             fields: dict[str, Any] | None = None) -> dict[str, Any]:
        fields = fields or {}
        with self._lock:
            sa = self._require()
            if self.run_id is None:
                self.run_id = self.db.start_run(identity=sa.identity)
            kwargs = {key: fields[key] for key in _CONFIGURE_KEYS
                      if key in fields and fields[key] is not None}
            if kwargs:
                sa.configure(**kwargs)
            try:
                sweep, peak, counter_hz, error_hz, counter_error = read_then_hold(
                    sa, label=label or None)
                sweep = Sweep(
                    amplitudes_dbm=sweep.amplitudes_dbm,
                    settings=sweep.settings,
                    trace=sweep.trace,
                    captured_at=sweep.captured_at,
                    label=label or f"{sweep.settings.center_hz / 1e6:.3f} MHz")
                screenshot_path = None
                screenshot_error = None
                try:
                    image = sa.save_screen_image(self._screenshot_path())
                    screenshot_path = str(image.resolve())
                except Exception as exc:
                    screenshot_error = str(exc)
                sweep_id = self.db.save_sweep(
                    self.run_id, sweep,
                    peak_hz=peak.frequency_hz, peak_dbm=peak.value,
                    counter_hz=counter_hz, frequency_error_hz=error_hz,
                    screenshot_path=screenshot_path)
                payload = _sweep_payload(sweep_id, sweep, self.db.load_sweep_row(sweep_id))
                payload["applied"] = bool(kwargs)
                payload["screenshot_error"] = screenshot_error
                payload["counter_error"] = counter_error
                return payload
            finally:
                try:
                    sa.continuous_sweep()
                except Exception:
                    pass

    def _screenshot_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return self.screenshot_dir / f"scan_{stamp}.png"

    def list_sweeps(self) -> list[dict[str, Any]]:
        return [_row_json(row) for row in self.db.list_sweeps()]

    def get_sweep(self, sweep_id: int) -> dict[str, Any]:
        sweep = self.db.load_sweep(sweep_id)
        return _sweep_payload(sweep_id, sweep, self.db.load_sweep_row(sweep_id))

    def screenshot_file(self, sweep_id: int) -> Path:
        row = self.db.load_sweep_row(sweep_id)
        stored = row["screenshot_path"]
        if not stored:
            raise FileNotFoundError(f"扫描 #{sweep_id} 没有截图")
        path = Path(stored)
        if not path.is_file():
            raise FileNotFoundError(f"截图文件已丢失：{path}")
        return path

    def clear_history(self) -> dict[str, Any]:
        with self._lock:
            paths = self.db.clear_history()
            for stored in paths:
                try:
                    Path(stored).unlink(missing_ok=True)
                except OSError:
                    pass
            if self.screenshot_dir.is_dir():
                for image in self.screenshot_dir.glob("scan_*.png"):
                    try:
                        image.unlink()
                    except OSError:
                        pass
            if self.sa is not None:
                self.run_id = self.db.start_run(identity=self.sa.identity)
            else:
                self.run_id = None
            return {"cleared": True, "run_id": self.run_id}


def _identity_json(identity: Identity) -> dict[str, str]:
    return {
        "vendor": identity.vendor,
        "model": identity.model,
        "serial": identity.serial,
        "firmware": identity.firmware,
        "text": str(identity),
    }


def _settings_json(settings: Settings) -> dict[str, Any]:
    return {
        "center_hz": settings.center_hz,
        "span_hz": settings.span_hz,
        "start_hz": settings.start_hz,
        "stop_hz": settings.stop_hz,
        "rbw_hz": settings.rbw_hz,
        "vbw_hz": settings.vbw_hz,
        "points": settings.points,
        "sweep_time_s": settings.sweep_time_s,
        "ref_level_dbm": settings.ref_level_dbm,
        "attenuation_db": settings.attenuation_db,
        "preamp": settings.preamp,
        "detector": settings.detector,
        "trace_type": settings.trace_type,
    }


def _row_json(row) -> dict[str, Any]:
    payload = {key: row[key] for key in row.keys()
               if key not in ("frequencies", "amplitudes")}
    path = payload.get("screenshot_path")
    payload["has_screenshot"] = bool(path)
    if path:
        payload["screenshot_name"] = Path(path).name
    return payload


def _sweep_payload(sweep_id: int, sweep: Sweep, row) -> dict[str, Any]:
    payload = _row_json(row)
    payload["id"] = sweep_id
    payload["settings"] = _settings_json(sweep.settings)
    payload["frequencies_hz"] = sweep.frequencies_hz.tolist()
    payload["amplitudes_dbm"] = sweep.amplitudes_dbm.tolist()
    return payload
