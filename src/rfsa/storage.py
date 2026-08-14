"""SQLite storage for measurement results.

Two tables:

``runs``   one row per test session (who, when, which instrument)
``sweeps`` one row per captured trace: settings, peak, optional frequency
           counter result, and amplitudes as a BLOB
"""

from __future__ import annotations

import sqlite3
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
    amplitudes         BLOB    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sweeps_run   ON sweeps(run_id);
CREATE INDEX IF NOT EXISTS idx_sweeps_label ON sweeps(label);
"""


class Storage:
    """Open (and create if needed) a measurement database."""

    def __init__(self, path: str | Path = "measurements.db"):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start_run(self, title: str | None = None, *, identity: Identity | None = None,
                  operator: str | None = None, notes: str | None = None) -> int:
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
        self.connection.execute("UPDATE runs SET finished_at = ? WHERE id = ?",
                                (utcnow().isoformat(), run_id))
        self.connection.commit()

    def save_sweep(self, run_id: int, sweep: Sweep, *,
                   counter_hz: float | None = None,
                   frequency_error_hz: float | None = None) -> int:
        """Store one sweep and optional frequency-counter results."""
        s = sweep.settings
        peak = sweep.peak
        columns = ("run_id", "captured_at", "label", "trace", *_SETTINGS_COLUMNS,
                   "start_hz", "stop_hz", "peak_hz", "peak_dbm",
                   "counter_hz", "frequency_error_hz", "amplitudes")
        values = (run_id, sweep.captured_at.isoformat(), sweep.label, sweep.trace,
                  *(getattr(s, column) for column in _SETTINGS_COLUMNS),
                  s.start_hz, s.stop_hz, peak.frequency_hz, peak.value,
                  counter_hz, frequency_error_hz,
                  np.asarray(sweep.amplitudes_dbm, dtype=_BLOB_DTYPE).tobytes())
        cursor = self.connection.execute(
            f"INSERT INTO sweeps ({', '.join(columns)})"
            f" VALUES ({', '.join('?' * len(columns))})", values)
        self.connection.commit()
        return int(cursor.lastrowid)

    def load_sweep(self, sweep_id: int) -> Sweep:
        row = self.connection.execute(
            "SELECT * FROM sweeps WHERE id = ?", (sweep_id,)).fetchone()
        if row is None:
            raise KeyError(f"no sweep with id {sweep_id}")
        values = {column: row[column] for column in _SETTINGS_COLUMNS}
        values["preamp"] = bool(values["preamp"])
        settings = Settings(**values)
        return Sweep(
            amplitudes_dbm=np.frombuffer(row["amplitudes"], dtype=_BLOB_DTYPE).copy(),
            settings=settings, trace=row["trace"],
            captured_at=datetime.fromisoformat(row["captured_at"]),
            label=row["label"])

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def peaks(self, run_id: int | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT id, captured_at, label, center_hz, rbw_hz, peak_hz, peak_dbm,"
               " counter_hz, frequency_error_hz FROM sweeps")
        params: tuple = ()
        if run_id is not None:
            sql += " WHERE run_id = ?"
            params = (run_id,)
        return self.query(sql + " ORDER BY captured_at", params)
