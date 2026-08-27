"""A stand-in for a pyvisa resource — enough to run the driver without hardware."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

# Frequency counter reads this many Hz above the peak marker.
_FCOUNT_OFFSET_HZ = 123.456

DEFAULTS = {
    "*IDN?": "Agilent Technologies,N9020A,MY49010001,A.14.16",
    "*OPC?": "1",
    ":SYSTem:ERRor?": '+0,"No error"',
    ":FORMat:DATA?": "REAL,64",
    ":FORMat:BORDer?": "SWAP",
    ":SENSe:FREQuency:CENTer?": "13250000000",
    ":SENSe:FREQuency:SPAN?": "26500000000",
    ":SENSe:BANDwidth:RESolution?": "3000000",
    ":SENSe:BANDwidth:VIDeo?": "3000000",
    ":SENSe:SWEep:POINts?": "1001",
    ":SENSe:SWEep:TIME?": "0.001",
    ":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel?": "0",
    ":SENSe:POWer:RF:ATTenuation?": "10",
    ":SENSe:POWer:RF:GAIN:STATe?": "0",
    ":SENSe:DETector:TRACe1?": "NORM",
    ":TRACe1:TYPE?": "WRIT",
    ":INITiate:CONTinuous?": "1",
}


def tone_trace(points: int = 1001, peak_index: int | None = None,
               peak_dbm: float = -20.0, floor_dbm: float = -95.0) -> np.ndarray:
    """Flat noise floor with one spike — kept for simple unit tests."""
    amplitudes = np.full(points, floor_dbm, dtype=float)
    amplitudes[points // 2 if peak_index is None else peak_index] = peak_dbm
    return amplitudes


def synthetic_trace(center_hz: float, span_hz: float, points: int, *,
                    peak_dbm: float = -20.5, floor_dbm: float = -92.0,
                    peak_offset_hz: float = 0.0, rbw_hz: float = 30e3,
                    seed: int = 42) -> np.ndarray:
    """A believable swept trace: ripple floor + RBW-shaped tone at centre."""
    rng = np.random.default_rng(seed)
    if points < 1:
        raise ValueError("points must be at least 1")
    if span_hz == 0:
        freqs = np.full(points, center_hz)
    else:
        start = center_hz - span_hz / 2
        stop = center_hz + span_hz / 2
        freqs = np.linspace(start, stop, points)

    noise = floor_dbm + rng.normal(0, 1.0, points)
    noise += 1.2 * np.sin(np.linspace(0, 6 * np.pi, points))
    peak_hz = center_hz + peak_offset_hz
    sigma = max(rbw_hz * 1.8, abs(span_hz) / 250 if span_hz else rbw_hz)
    bump = (peak_dbm - floor_dbm) * np.exp(-0.5 * ((freqs - peak_hz) / sigma) ** 2)
    amplitudes = noise + bump
    amplitudes[int(np.argmin(np.abs(freqs - peak_hz)))] = peak_dbm
    return amplitudes.astype(float)


def fake_screenshot_png(trace: np.ndarray, *, center_hz: float, span_hz: float,
                        width: int = 640, height: int = 360) -> bytes:
    """Minimal spectrum-analyser style PNG for fake screen captures."""
    bg = np.array([26, 26, 46], dtype=np.uint8)
    grid = np.array([55, 55, 75], dtype=np.uint8)
    trace_color = np.array([255, 215, 60], dtype=np.uint8)

    img = np.tile(bg, (height, width, 1))
    top, bottom, left, right = 36, 28, 48, 16
    for y in range(top, height - bottom, 32):
        img[y, left:width - right] = grid
    for x in range(left, width - right, 64):
        img[top:height - bottom, x] = grid

    plot_w = width - left - right
    plot_h = height - top - bottom
    ymin, ymax = -100.0, -10.0
    clipped = np.clip(trace, ymin, ymax)
    ys = top + (1.0 - (clipped - ymin) / (ymax - ymin)) * (plot_h - 1)
    xs = np.linspace(left, width - right - 1, len(trace))
    for x, y in zip(xs.astype(int), ys.astype(int)):
        for dy in (-1, 0, 1):
            yy = y + dy
            if top <= yy < height - bottom and left <= x < width - right:
                img[yy, x] = trace_color

    return _encode_png_rgb(img)


def _encode_png_rgb(pixels: np.ndarray) -> bytes:
    height, width, _ = pixels.shape
    raw = b"".join(
        b"\x00" + pixels[row].astype(np.uint8).tobytes() for row in range(height))
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


@dataclass
class FakeSettings:
    center_hz: float = 13.25e9
    span_hz: float = 26.5e9
    rbw_hz: float = 3e6
    vbw_hz: float = 3e6
    points: int = 1001
    sweep_time_s: float = 0.001
    ref_level_dbm: float = 0.0
    attenuation_db: float = 10.0
    preamp: bool = False
    detector: str = "NORM"
    trace_type: str = "WRIT"
    peak_dbm: float = -20.5
    peak_offset_hz: float = 0.0
    marker_hz: float | None = field(default=None)
    marker_dbm: float | None = field(default=None)
    counter_hz: float | None = field(default=None)


def _is_sentinel(value: str | None) -> bool:
    if value is None:
        return False
    try:
        return abs(float(value)) >= 9.9e37
    except (TypeError, ValueError):
        return False


class FakeResource:
    """Duck-types the pyvisa methods the driver uses."""

    def __init__(self, responses: dict | None = None, trace: np.ndarray | None = None,
                 errors: list[str] | None = None, *, settings: FakeSettings | None = None):
        self.settings = settings or FakeSettings()
        self.responses = dict(DEFAULTS)
        self.responses.update(responses or {})
        self._manual_trace = trace is not None
        self.trace = trace if trace is not None else self._build_trace()
        self.files: dict[str, bytes] = {}
        self.error_queue = list(errors or [])
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.binary_reads: list[dict] = []
        self.timeout = 10000
        self.read_termination = "\n"
        self.write_termination = "\n"
        self.closed = False
        self.continuous = True
        if not self._manual_trace:
            self._place_marker_on_peak()
        else:
            self._sync_responses()

    @classmethod
    def for_preset(cls, name: str = "1 GHz") -> "FakeResource":
        from .presets import preset as lookup_preset

        chosen = lookup_preset(name)
        settings = FakeSettings(
            center_hz=chosen.center_hz,
            span_hz=chosen.span_hz,
            rbw_hz=chosen.rbw_hz,
            points=chosen.points,
            attenuation_db=chosen.attenuation_db,
            detector=chosen.detector,
            peak_dbm=-20.5,
        )
        resource = cls(settings=settings)
        return resource

    def write(self, command: str) -> None:
        self.writes.append(command)
        if command.startswith(":MMEM:STOR:SCR ") or command.startswith(
                ":MMEMory:STORe:SCReen "):
            self.files[self._file_arg(command)] = self._screenshot_bytes()
            return
        if command.startswith(":MMEM:DEL ") or command.startswith(":MMEMory:DELete "):
            self.files.pop(self._file_arg(command), None)
            return
        if command.startswith(":CALCulate:MARKer1:MAXimum"):
            self._place_marker_on_peak()
            return

        head, _, argument = command.partition(" ")
        if not argument:
            return
        value = argument.strip().strip("'\"")
        if head == ":SENSe:FREQuency:CENTer":
            self.settings.center_hz = float(value)
        elif head == ":SENSe:FREQuency:SPAN":
            self.settings.span_hz = float(value)
        elif head == ":SENSe:BANDwidth:RESolution":
            self.settings.rbw_hz = float(value)
        elif head == ":SENSe:BANDwidth:VIDeo":
            self.settings.vbw_hz = float(value)
        elif head == ":SENSe:SWEep:POINts":
            new_points = int(float(value))
            if self._manual_trace and new_points > 0 and new_points != len(self.trace):
                old = np.linspace(0.0, 1.0, len(self.trace))
                new = np.linspace(0.0, 1.0, new_points)
                self.trace = np.interp(new, old, self.trace)
            self.settings.points = new_points
        elif head == ":SENSe:SWEep:TIME":
            self.settings.sweep_time_s = float(value)
        elif head == ":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel":
            self.settings.ref_level_dbm = float(value)
        elif head == ":SENSe:POWer:RF:ATTenuation":
            self.settings.attenuation_db = float(value)
        elif head == ":SENSe:POWer:RF:GAIN:STATe":
            self.settings.preamp = value in ("1", "ON", "TRUE")
        elif head == ":SENSe:DETector:TRACe1":
            self.settings.detector = value.upper()[:4].rstrip()
        elif head == ":CALCulate:MARKer1:X":
            self.settings.marker_hz = float(value)
            idx = int(np.argmin(np.abs(self._frequency_axis() - float(value))))
            self.settings.marker_dbm = float(self.trace[idx])
        elif head == ":CALCulate:MARKer1:FCOunt:PRECision":
            return
        elif head.startswith(":CALCulate:MARKer1:FCOunt:STATe"):
            if value in ("1", "ON", "TRUE") and self.settings.marker_hz is not None:
                self.settings.counter_hz = self.settings.marker_hz + _FCOUNT_OFFSET_HZ
            return
        elif head == ":INITiate:CONTinuous":
            self.continuous = value in ("1", "ON", "TRUE")

        if head.endswith("?"):
            self.responses[head] = value
        self._refresh_trace_if_needed(head)
        self._sync_responses()

    def query(self, command: str) -> str:
        self.queries.append(command)
        return self._answer(command)

    def query_binary_values(self, command, datatype="d", is_big_endian=False,
                            container=np.array, **_kwargs):
        self.queries.append(command)
        self.binary_reads.append({"command": command, "datatype": datatype,
                                  "is_big_endian": is_big_endian,
                                  "read_termination": self.read_termination,
                                  "timeout": self.timeout})
        if command.startswith(":HCOPy:SDUMp:DATA"):
            return container(self._screenshot_bytes())
        if command.startswith(":MMEM:DATA?") or command.startswith(":MMEMory:DATA?"):
            return container(self.files[self._file_arg(command)])
        return container(self.trace)

    def close(self) -> None:
        self.closed = True

    def _answer(self, command: str) -> str:
        command = command.strip()
        if command == ":SYSTem:ERRor?":
            return self.error_queue.pop(0) if self.error_queue else '+0,"No error"'
        if command.startswith(":TRACe:DATA?"):
            return ",".join(f"{value:.6f}" for value in self.trace)
        if command in self.responses:
            return self.responses[command]
        raise KeyError(f"FakeResource has no answer for {command!r}")

    def _file_arg(self, command: str) -> str:
        return command.partition(" ")[2].strip().strip("\"'")

    def written(self, prefix: str) -> list[str]:
        return [c for c in self.writes if c.startswith(prefix)]

    def last(self, prefix: str) -> str:
        matches = self.written(prefix)
        assert matches, f"nothing was written starting with {prefix!r}"
        return matches[-1]

    def _frequency_axis(self) -> np.ndarray:
        s = self.settings
        n = len(self.trace)
        if s.span_hz == 0:
            return np.full(n, s.center_hz)
        return np.linspace(s.center_hz - s.span_hz / 2,
                           s.center_hz + s.span_hz / 2, n)

    def _build_trace(self) -> np.ndarray:
        s = self.settings
        return synthetic_trace(
            s.center_hz, s.span_hz, s.points,
            peak_dbm=s.peak_dbm, peak_offset_hz=s.peak_offset_hz, rbw_hz=s.rbw_hz)

    def _refresh_trace_if_needed(self, head: str) -> None:
        if head in (":SENSe:FREQuency:CENTer", ":SENSe:FREQuency:SPAN",
                    ":SENSe:BANDwidth:RESolution", ":SENSe:SWEep:POINts"):
            if not self._manual_trace:
                self.trace = self._build_trace()
            self._place_marker_on_peak()

    def _place_marker_on_peak(self) -> None:
        index = int(np.argmax(self.trace))
        axis = self._frequency_axis()
        self.settings.marker_hz = float(axis[index])
        self.settings.marker_dbm = float(self.trace[index])
        self.settings.counter_hz = self.settings.marker_hz + _FCOUNT_OFFSET_HZ
        self._sync_responses()

    def _sync_responses(self) -> None:
        s = self.settings
        self.responses.update({
            ":SENSe:FREQuency:CENTer?": f"{s.center_hz:.3f}",
            ":SENSe:FREQuency:SPAN?": f"{s.span_hz:.3f}",
            ":SENSe:BANDwidth:RESolution?": f"{s.rbw_hz:.6g}",
            ":SENSe:BANDwidth:VIDeo?": f"{s.vbw_hz:.6g}",
            ":SENSe:SWEep:POINts?": str(s.points),
            ":SENSe:SWEep:TIME?": f"{s.sweep_time_s:g}",
            ":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel?": f"{s.ref_level_dbm:g}",
            ":SENSe:POWer:RF:ATTenuation?": f"{s.attenuation_db:g}",
            ":SENSe:POWer:RF:GAIN:STATe?": "1" if s.preamp else "0",
            ":SENSe:DETector:TRACe1?": s.detector,
            ":TRACe1:TYPE?": s.trace_type,
            ":INITiate:CONTinuous?": "1" if self.continuous else "0",
        })
        if s.marker_hz is not None:
            self.responses[":CALCulate:MARKer1:X?"] = f"{s.marker_hz:.3f}"
        if s.marker_dbm is not None:
            existing_y = self.responses.get(":CALCulate:MARKer1:Y?")
            if not _is_sentinel(existing_y):
                self.responses[":CALCulate:MARKer1:Y?"] = f"{s.marker_dbm:.6f}"
        if s.counter_hz is not None:
            existing_c = self.responses.get(":CALCulate:MARKer1:FCOunt:X?")
            if not _is_sentinel(existing_c):
                self.responses[":CALCulate:MARKer1:FCOunt:X?"] = f"{s.counter_hz:.3f}"

    def _screenshot_bytes(self) -> bytes:
        return fake_screenshot_png(
            self.trace,
            center_hz=self.settings.center_hz,
            span_hz=self.settings.span_hz,
        )


def fake_resource(*, preset: str | None = "1 GHz") -> FakeResource:
    """Ready-to-use fake instrument. Default: the 1 GHz frequency-check preset."""
    if preset is None:
        return FakeResource()
    return FakeResource.for_preset(preset)
