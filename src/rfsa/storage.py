"""SQLite storage for measurement results.

Two tables:

``runs``   one row per test session (when, which instrument)
``sweeps`` one row per captured trace: settings, peak, optional frequency
           counter result, and trace x/y coordinates as BLOBs
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
_TRACE_BLOBS = ("frequencies", "amplitudes")
_SETTINGS_COLUMNS = tuple(f.name for f in fields(Settings))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
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
    frequencies        BLOB    NOT NULL,
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
        sweep_columns = {row["name"] for row in
                         self.connection.execute("PRAGMA table_info(sweeps)")}
        if "screenshot_path" not in sweep_columns:
            self.connection.execute(
                "ALTER TABLE sweeps ADD COLUMN screenshot_path TEXT")
        if "frequencies" not in sweep_columns:
            self.connection.execute("ALTER TABLE sweeps ADD COLUMN frequencies BLOB")
            self._backfill_frequencies()
        run_columns = {row["name"] for row in
                       self.connection.execute("PRAGMA table_info(runs)")}
        for obsolete in ("title", "operator"):
            if obsolete in run_columns:
                self.connection.execute(f"ALTER TABLE runs DROP COLUMN {obsolete}")

    def _backfill_frequencies(self) -> None:
        """Older databases stored only y values; derive x from the saved settings."""
        rows = self.connection.execute(
            "SELECT id, center_hz, span_hz, points, amplitudes FROM sweeps"
            " WHERE frequencies IS NULL").fetchall()
        for row in rows:
            start_hz = row["center_hz"] - row["span_hz"] / 2
            stop_hz = row["center_hz"] + row["span_hz"] / 2
            points = len(np.frombuffer(row["amplitudes"], dtype=_BLOB_DTYPE))
            if row["span_hz"] == 0:
                axis = np.full(points, row["center_hz"], dtype=float)
            else:
                axis = np.linspace(start_hz, stop_hz, points)
            self.connection.execute(
                "UPDATE sweeps SET frequencies = ? WHERE id = ?",
                (np.asarray(axis, dtype=_BLOB_DTYPE).tobytes(), row["id"]))

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start_run(self, *, identity: Identity | None = None, notes: str | None = None) -> int:
        with self._lock:
            cursor = self.connection.execute(
                "INSERT INTO runs (started_at, model, serial, firmware, notes)"
                " VALUES (?, ?, ?, ?, ?)",
                (utcnow().isoformat(),
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
        frequencies = np.asarray(sweep.frequencies_hz, dtype=_BLOB_DTYPE)
        amplitudes = np.asarray(sweep.amplitudes_dbm, dtype=_BLOB_DTYPE)
        if len(frequencies) != len(amplitudes):
            raise ValueError(
                f"trace has {len(amplitudes)} amplitudes but {len(frequencies)} frequencies")
        columns = ("run_id", "captured_at", "label", "trace", *_SETTINGS_COLUMNS,
                   "start_hz", "stop_hz", "peak_hz", "peak_dbm",
                   "counter_hz", "frequency_error_hz", "screenshot_path",
                   "frequencies", "amplitudes")
        values = (run_id, sweep.captured_at.isoformat(), sweep.label, sweep.trace,
                  *(getattr(s, column) for column in _SETTINGS_COLUMNS),
                  s.start_hz, s.stop_hz, stored_hz, stored_dbm,
                  counter_hz, frequency_error_hz, screenshot_path,
                  frequencies.tobytes(), amplitudes.tobytes())
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
        frequencies = np.frombuffer(row["frequencies"], dtype=_BLOB_DTYPE).copy()
        amplitudes = np.frombuffer(row["amplitudes"], dtype=_BLOB_DTYPE).copy()
        return Sweep(
            amplitudes_dbm=amplitudes,
            settings=settings, trace=row["trace"],
            captured_at=datetime.fromisoformat(row["captured_at"]),
            label=row["label"],
            _frequency_axis_hz=frequencies)

    def load_trace(self, sweep_id: int) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(frequencies_hz, amplitudes_dbm)`` exactly as stored."""
        row = self.load_sweep_row(sweep_id)
        frequencies = np.frombuffer(row["frequencies"], dtype=_BLOB_DTYPE).copy()
        amplitudes = np.frombuffer(row["amplitudes"], dtype=_BLOB_DTYPE).copy()
        return frequencies, amplitudes

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
            " s.counter_hz, s.frequency_error_hz, s.screenshot_path"
            " FROM sweeps s"
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
