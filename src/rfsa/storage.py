"""SQLite storage for measurement results.

Two tables:

``runs``   one row per test session (who, when, which instrument)
``sweeps`` one row per captured trace: settings, peak, optional frequency
           counter result, and amplitudes as a BLOB
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import numpy as np

from .models import Identity, Settings, Sweep, utcnow

_BLOB_DTYPE = np.dtype("<f8")
_SETTINGS_COLUMNS = tuple(f.name for f in fields(Settings))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    title       TEXT,
    operator    TEXT,
    model       TEXT,
    serial      TEXT,
    firmware    TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS sweeps (
    id                 INTEGER PRIMARY KEY,
    run_id             INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    captured_at        TEXT    NOT NULL,
    label              TEXT,
    trace              INTEGER NOT NULL DEFAULT 1,
    center_hz          REAL    NOT NULL,
    span_hz            REAL    NOT NULL,
    start_hz           REAL    NOT NULL,
    stop_hz            REAL    NOT NULL,
    rbw_hz             REAL    NOT NULL,
    vbw_hz             REAL    NOT NULL,
    points             INTEGER NOT NULL,
    sweep_time_s       REAL    NOT NULL,
    ref_level_dbm      REAL    NOT NULL,
    attenuation_db     REAL    NOT NULL,
    preamp             INTEGER NOT NULL,
    detector           TEXT    NOT NULL,
    trace_type         TEXT    NOT NULL,
    peak_hz            REAL,
    peak_dbm           REAL,
    counter_hz         REAL,
    frequency_error_hz REAL,
    screenshot_path    TEXT,
    amplitudes         BLOB    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sweeps_run   ON sweeps(run_id);
CREATE INDEX IF NOT EXISTS idx_sweeps_label ON sweeps(label);
"""


class Storage:
    """Open (and create if needed) a measurement database."""

    def __init__(self, path: str | Path = "measurements.db"):
        self.path = str(path)
        self._lock = threading.Lock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate()
        self.connection.commit()

    def _migrate(self) -> None:
        columns = {row["name"] for row in
                   self.connection.execute("PRAGMA table_info(sweeps)")}
        if "screenshot_path" not in columns:
            self.connection.execute(
                "ALTER TABLE sweeps ADD COLUMN screenshot_path TEXT")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start_run(self, title: str | None = None, *, identity: Identity | None = None,
                  operator: str | None = None, notes: str | None = None) -> int:
        with self._lock:
            cursor = self.connection.execute(
                "INSERT INTO runs (started_at, title, operator, model, serial,"
                " firmware, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (utcnow().isoformat(), title, operator,
                 identity.model if identity else None,
                 identity.serial if identity else None,
                 identity.firmware if identity else None, notes))
            self.connection.commit()
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int) -> None:
        with self._lock:
            self.connection.execute("UPDATE runs SET finished_at = ? WHERE id = ?",
                                    (utcnow().isoformat(), run_id))
            self.connection.commit()

    def clear_history(self) -> list[str]:
        """Delete every run and sweep. Returns screenshot paths that were stored."""
        rows = self.query(
            "SELECT screenshot_path FROM sweeps WHERE screenshot_path IS NOT NULL")
        paths = [row["screenshot_path"] for row in rows if row["screenshot_path"]]
        with self._lock:
            self.connection.execute("DELETE FROM sweeps")
            self.connection.execute("DELETE FROM runs")
            self.connection.commit()
        return paths

    def save_sweep(self, run_id: int, sweep: Sweep, *,
                   counter_hz: float | None = None,
                   frequency_error_hz: float | None = None,
                   peak_hz: float | None = None,
                   peak_dbm: float | None = None,
                   screenshot_path: str | None = None) -> int:
        """Store one sweep and optional frequency-counter results.

        ``peak_hz`` / ``peak_dbm`` override the trace-bin peak when the
        instrument marker (or frequency counter) is the value worth keeping.
        """
        s = sweep.settings
        peak = sweep.peak
        stored_hz = peak.frequency_hz if peak_hz is None else peak_hz
        stored_dbm = peak.value if peak_dbm is None else peak_dbm
        columns = ("run_id", "captured_at", "label", "trace", *_SETTINGS_COLUMNS,
                   "start_hz", "stop_hz", "peak_hz", "peak_dbm",
                   "counter_hz", "frequency_error_hz", "screenshot_path",
                   "amplitudes")
        values = (run_id, sweep.captured_at.isoformat(), sweep.label, sweep.trace,
                  *(getattr(s, column) for column in _SETTINGS_COLUMNS),
                  s.start_hz, s.stop_hz, stored_hz, stored_dbm,
                  counter_hz, frequency_error_hz, screenshot_path,
                  np.asarray(sweep.amplitudes_dbm, dtype=_BLOB_DTYPE).tobytes())
        with self._lock:
            cursor = self.connection.execute(
                f"INSERT INTO sweeps ({', '.join(columns)})"
                f" VALUES ({', '.join('?' * len(columns))})", values)
            self.connection.commit()
            return int(cursor.lastrowid)

    def load_sweep(self, sweep_id: int) -> Sweep:
        row = self.load_sweep_row(sweep_id)
        values = {column: row[column] for column in _SETTINGS_COLUMNS}
        values["preamp"] = bool(values["preamp"])
        settings = Settings(**values)
        return Sweep(
            amplitudes_dbm=np.frombuffer(row["amplitudes"], dtype=_BLOB_DTYPE).copy(),
            settings=settings, trace=row["trace"],
            captured_at=datetime.fromisoformat(row["captured_at"]),
            label=row["label"])

    def load_sweep_row(self, sweep_id: int) -> sqlite3.Row:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM sweeps WHERE id = ?", (sweep_id,)).fetchone()
        if row is None:
            raise KeyError(f"no sweep with id {sweep_id}")
        return row

    def list_sweeps(self) -> list[sqlite3.Row]:
        """Every stored sweep, newest first, with enough fields to tell them apart."""
        return self.query(
            "SELECT s.id, s.run_id, s.captured_at, s.label, s.center_hz, s.span_hz,"
            " s.start_hz, s.stop_hz, s.rbw_hz, s.vbw_hz, s.points, s.sweep_time_s,"
            " s.attenuation_db, s.ref_level_dbm, s.detector, s.peak_hz, s.peak_dbm,"
            " s.counter_hz, s.frequency_error_hz, s.screenshot_path,"
            " r.title, r.operator"
            " FROM sweeps s LEFT JOIN runs r ON r.id = s.run_id"
            " ORDER BY s.captured_at DESC, s.id DESC")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.connection.execute(sql, params).fetchall()

    def peaks(self, run_id: int | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT id, captured_at, label, center_hz, rbw_hz, peak_hz, peak_dbm,"
               " counter_hz, frequency_error_hz FROM sweeps")
        params: tuple = ()
        if run_id is not None:
            sql += " WHERE run_id = ?"
            params = (run_id,)
        return self.query(sql + " ORDER BY captured_at", params)
