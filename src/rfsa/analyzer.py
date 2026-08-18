from __future__ import annotations

import math
import re
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .errors import ConnectionFailed, InstrumentError, ParameterError, ScpiError
from .limits import Limits
from .models import DETECTORS, Identity, Reading, Settings, Sweep, utcnow

_ERROR_RE = re.compile(r'^\s*([+-]?\d+)\s*,\s*"?(.*?)"?\s*$')
_DEFAULT_TIMEOUT_S = 10.0
_SCREENSHOT_TIMEOUT_S = 30.0
_SCREENSHOT_REMOTE = r"D:\rfsa_screen.png"
_MAX_ERROR_DRAIN = 20

_ON = ("1", "ON", "TRUE")
_FCOUNT_PRECISIONS = {"FINE": "FINe", "MEDIUM": "MEDium", "COARSE": "COARse"}

_SETTINGS_QUERIES = (
    ("center_hz",      ":SENSe:FREQuency:CENTer?",              float),
    ("span_hz",        ":SENSe:FREQuency:SPAN?",                float),
    ("rbw_hz",         ":SENSe:BANDwidth:RESolution?",          float),
    ("vbw_hz",         ":SENSe:BANDwidth:VIDeo?",               float),
    ("points",         ":SENSe:SWEep:POINts?",                  lambda a: int(float(a))),
    ("sweep_time_s",   ":SENSe:SWEep:TIME?",                    float),
    ("ref_level_dbm",  ":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel?", float),
    ("attenuation_db", ":SENSe:POWer:RF:ATTenuation?",          float),
    ("preamp",         ":SENSe:POWer:RF:GAIN:STATe?",           lambda a: a.upper() in _ON),
    ("detector",       ":SENSe:DETector:TRACe1?",               str.upper),
    ("trace_type",     ":TRACe1:TYPE?",                         str.upper),
)


def _yes_no(answer: str) -> bool:
    return answer.upper() in _ON


def is_invalid_scpi_value(value: float) -> bool:
    """Keysight fills 9.91e37 when a query has no data."""
    try:
        return abs(float(value)) >= 9.9e37
    except (TypeError, ValueError):
        return True


def open_visa(address: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S,
              visa_library: str = ""):
    """Open a VISA session."""
    try:
        import pyvisa
    except ImportError as exc:  # pragma: no cover
        raise ConnectionFailed("PyVISA is not installed (pip install pyvisa)") from exc
    try:
        resource = pyvisa.ResourceManager(visa_library).open_resource(address)
    except Exception as exc:
        raise ConnectionFailed(f"cannot open {address!r}: {exc}") from exc
    resource.timeout = int(timeout_s * 1000)
    resource.read_termination = "\n"
    resource.write_termination = "\n"
    return resource


def connect(address: str, **kwargs) -> "N9020A":
    """``connect("TCPIP0::192.168.10.2::inst0::SOCKET")``."""
    timeout_s = kwargs.pop("timeout_s", _DEFAULT_TIMEOUT_S)
    return N9020A(open_visa(address, timeout_s=timeout_s), **kwargs)


class N9020A:
    """One analyzer session.

    Connecting changes nothing on the instrument — call :meth:`prepare`,
    :meth:`configure`, or :meth:`preset` explicitly when you mean to.
    """

    def __init__(self, resource, *, limits: Limits | None = None,
                 verify_model: bool = True, prepare: bool = False):
        self._res = resource
        self.identity = Identity.parse(self.query("*IDN?"))
        if verify_model and self.identity.model.upper() != "N9020A":
            raise InstrumentError(
                f"expected an N9020A, found {self.identity.model!r}")
        self.limits = limits or Limits()
        self._data_format = self._read_data_format()
        self._byte_order = self._read_byte_order()
        if prepare:
            self.prepare()

    def _read_data_format(self) -> str:
        fmt = self.query(":FORMat:DATA?").upper().replace(" ", "")
        if fmt.startswith("ASC"):
            return "ASC"
        return "REAL,64" if fmt.endswith("64") else "REAL,32"

    def _read_byte_order(self) -> str:
        """``SWAP`` is little-endian, ``NORM`` big-endian.

        Guessing here is worse than failing: the wrong order turns every
        amplitude into a plausible-looking number, with nothing to catch it.
        """
        return "SWAP" if self.query(":FORMat:BORDer?").upper().startswith("SWAP") \
            else "NORM"

    @property
    def data_format(self) -> str:
        return self._data_format

    @property
    def byte_order(self) -> str:
        return self._byte_order

    def write(self, command: str) -> None:
        self._res.write(command)

    def write_bool(self, command: str, value: bool) -> None:
        self.write(f"{command} {int(bool(value))}")

    def query(self, command: str) -> str:
        return self._res.query(command).strip()

    def query_float(self, command: str) -> float:
        return float(self.query(command))

    def query_int(self, command: str) -> int:
        return int(float(self.query(command)))

    def opc(self, timeout_s: float | None = None) -> None:
        if timeout_s is None:
            self.query("*OPC?")
            return
        previous = self._res.timeout
        self._res.timeout = int(timeout_s * 1000)
        try:
            self.query("*OPC?")
        finally:
            self._res.timeout = previous

    def errors(self) -> list[ScpiError]:
        found = []
        for _ in range(_MAX_ERROR_DRAIN):
            match = _ERROR_RE.match(self.query(":SYSTem:ERRor?"))
            if not match:
                break
            code, message = int(match.group(1)), match.group(2)
            if code == 0:
                break
            found.append(ScpiError(code, message))
        return found

    def check_errors(self, command: str | None = None) -> None:
        found = self.errors()
        if found:
            raise ScpiError(found[0].code, found[0].message, command)

    def prepare(self) -> None:
        """Switch to Swept SA and select binary transfers."""
        self.write(":INSTrument:SELect SA")
        self.write(":FORMat:DATA REAL,64")
        self._data_format = "REAL,64"
        self.write(":FORMat:BORDer SWAP")
        self._byte_order = "SWAP"
        self.check_errors()

    def preset(self) -> None:
        """``*RST`` — wipes every measurement setting."""
        self.write("*RST")
        self.opc(timeout_s=30.0)
        self._data_format = self._read_data_format()
        self._byte_order = self._read_byte_order()

    def close(self) -> None:
        try:
            self.continuous_sweep()
        except Exception:
            pass
        self._res.close()

    def __enter__(self) -> "N9020A":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<N9020A {self.identity}>"

    def _check(self, name: str, value, low, high, unit: str = "") -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ParameterError(f"{name} must be a number, got {value!r}") from None
        if not math.isfinite(number):
            raise ParameterError(f"{name} must be finite, got {value!r}")
        if not low <= number <= high:
            raise ParameterError(
                f"{name} must be within [{low:g}, {high:g}]{unit}, got {number:g}{unit}")
        return number

    def _check_int(self, name: str, value, low: int, high: int) -> int:
        number = self._check(name, value, low, high)
        if number != int(number):
            raise ParameterError(f"{name} must be a whole number, got {value!r}")
        return int(number)

    def _require_data(self, value: float, what: str) -> float:
        if is_invalid_scpi_value(value):
            raise InstrumentError(
                f"{what}: the analyzer answered 9.91e37, its code for 'no data'")
        return value

    @contextmanager
    def _binary_read(self, timeout_s: float | None = None):
        """Read raw bytes with the termination character disabled.

        Any byte of a REAL,64 sample or a PNG can equal 0x0A, the newline the
        text replies end with. Leaving it enabled truncates the transfer part
        way through, so the reply has to be delimited by its length header
        alone.
        """
        previous_term = getattr(self._res, "read_termination", "\n")
        previous_timeout = self._res.timeout
        self._res.read_termination = None
        if timeout_s is not None:
            self._res.timeout = int(timeout_s * 1000)
        try:
            yield
        finally:
            self._res.read_termination = previous_term
            self._res.timeout = previous_timeout

    @property
    def center_hz(self) -> float:
        return self.query_float(":SENSe:FREQuency:CENTer?")

    @center_hz.setter
    def center_hz(self, value: float) -> None:
        value = self._check("center frequency", value, self.limits.freq_min_hz,
                            self.limits.freq_max_hz, " Hz")
        self.write(f":SENSe:FREQuency:CENTer {value:.3f}")

    @property
    def span_hz(self) -> float:
        return self.query_float(":SENSe:FREQuency:SPAN?")

    @span_hz.setter
    def span_hz(self, value: float) -> None:
        full = self.limits.freq_max_hz - self.limits.freq_min_hz
        span = self._check("span", value, 0.0, full, " Hz")
        if span != 0:
            self._check("span", span, self.limits.span_min_hz, full, " Hz")
        self.write(f":SENSe:FREQuency:SPAN {span:.3f}")

    def set_frequency(self, center_hz: float, span_hz: float) -> None:
        """Set centre and span. Span goes first to avoid band-edge coupling."""
        self._check_window(center_hz, span_hz)
        self.span_hz = span_hz
        self.center_hz = center_hz

    def _check_window(self, center_hz: float, span_hz: float) -> None:
        """Refuse a centre/span pair whose upper edge is off the instrument.

        Both numbers can be legal alone while the window they describe is not.
        The analyzer clips such a request and sweeps a different band without
        reporting anything.
        """
        try:
            stop_hz = float(center_hz) + float(span_hz) / 2
        except (TypeError, ValueError):
            return  # the individual setters report what is wrong with the value
        if stop_hz > self.limits.freq_max_hz:
            raise ParameterError(
                f"center {float(center_hz):g} Hz with span {float(span_hz):g} Hz "
                f"ends at {stop_hz:g} Hz, above the "
                f"{self.limits.freq_max_hz:g} Hz limit")

    @property
    def rbw_hz(self) -> float:
        return self.query_float(":SENSe:BANDwidth:RESolution?")

    @rbw_hz.setter
    def rbw_hz(self, value: float) -> None:
        value = self._check("RBW", value, self.limits.rbw_min_hz,
                            self.limits.rbw_max_hz, " Hz")
        self.write(f":SENSe:BANDwidth:RESolution {value:.6g}")

    @property
    def vbw_hz(self) -> float:
        return self.query_float(":SENSe:BANDwidth:VIDeo?")

    @vbw_hz.setter
    def vbw_hz(self, value: float) -> None:
        value = self._check("VBW", value, self.limits.vbw_min_hz,
                            self.limits.vbw_max_hz, " Hz")
        self.write(f":SENSe:BANDwidth:VIDeo {value:.6g}")

    @property
    def ref_level_dbm(self) -> float:
        return self.query_float(":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel?")

    @ref_level_dbm.setter
    def ref_level_dbm(self, value: float) -> None:
        value = self._check("reference level", value, self.limits.ref_level_min_dbm,
                            self.limits.ref_level_max_dbm, " dBm")
        self.write(f":DISPlay:WINDow:TRACe:Y:SCALe:RLEVel {value:g}")

    @property
    def attenuation_db(self) -> float:
        return self.query_float(":SENSe:POWer:RF:ATTenuation?")

    @attenuation_db.setter
    def attenuation_db(self, value: float) -> None:
        step = self.limits.atten_step_db
        value = self._check("attenuation", value, self.limits.atten_min_db,
                            self.limits.atten_max_db, " dB")
        if not math.isclose(value % step, 0.0, abs_tol=1e-9):
            raise ParameterError(
                f"attenuation must be a multiple of {step:g} dB, got {value:g} dB")
        self.write(f":SENSe:POWer:RF:ATTenuation {value:g}")

    @property
    def preamp_on(self) -> bool:
        return _yes_no(self.query(":SENSe:POWer:RF:GAIN:STATe?"))

    @preamp_on.setter
    def preamp_on(self, value: bool) -> None:
        if value and not self.limits.has_preamp:
            raise ParameterError("no preamp option installed")
        self.write_bool(":SENSe:POWer:RF:GAIN:STATe", value)

    @property
    def points(self) -> int:
        return self.query_int(":SENSe:SWEep:POINts?")

    @points.setter
    def points(self, value: int) -> None:
        number = self._check_int("sweep points", value, self.limits.points_min,
                                 self.limits.points_max)
        self.write(f":SENSe:SWEep:POINts {number}")

    @property
    def sweep_time_s(self) -> float:
        return self.query_float(":SENSe:SWEep:TIME?")

    @sweep_time_s.setter
    def sweep_time_s(self, value: float) -> None:
        low, high = self.limits.sweep_time_range(self.span_hz)
        value = self._check("sweep time", value, low, high, " s")
        self.write(f":SENSe:SWEep:TIME {value:g}")

    @property
    def detector(self) -> str:
        return self.query(":SENSe:DETector:TRACe1?").upper()

    @detector.setter
    def detector(self, value: str) -> None:
        kind = str(value).upper()[:4].rstrip()
        if kind not in DETECTORS:
            raise ParameterError(f"detector must be one of {DETECTORS}, got {value!r}")
        self.write(f":SENSe:DETector:TRACe1 {kind}")

    def single_sweep(self, timeout_s: float | None = None) -> None:
        if timeout_s is None:
            timeout_s = max(10.0, self.sweep_time_s + 5.0)
        self.write_bool(":INITiate:CONTinuous", False)
        self.write(":INITiate:IMMediate")
        self.opc(timeout_s=timeout_s)
        self.check_errors(":INITiate:IMMediate")

    def continuous_sweep(self) -> None:
        """Free-run again so the front panel does not stay frozen."""
        self.write_bool(":INITiate:CONTinuous", True)

    def settings(self) -> Settings:
        parsed = {}
        for name, query, parse in _SETTINGS_QUERIES:
            parsed[name] = parse(self.query(query))
        return Settings(**parsed)

    def read_trace(self, trace: int = 1, *, label: str | None = None) -> Sweep:
        trace = self._check_int("trace", trace, 1, self.limits.traces)
        command = f":TRACe:DATA? TRACE{trace}"
        if self._data_format == "ASC":
            amplitudes = np.array([float(v) for v in self.query(command).split(",")])
        else:
            with self._binary_read():
                amplitudes = np.asarray(self._res.query_binary_values(
                    command, datatype="d" if self._data_format == "REAL,64" else "f",
                    is_big_endian=self._byte_order == "NORM", container=np.array),
                    dtype=float)
        settings = self.settings()
        if len(amplitudes) != settings.points:
            raise InstrumentError(
                f"got {len(amplitudes)} points, analyzer reports {settings.points}")
        return Sweep(amplitudes_dbm=amplitudes, settings=settings, trace=trace,
                     captured_at=utcnow(), label=label)

    def capture(self, *, label: str | None = None,
                timeout_s: float | None = None) -> Sweep:
        self.single_sweep(timeout_s=timeout_s)
        return self.read_trace(1, label=label)

    def peak_search(self, marker: int = 1) -> Reading:
        marker = self._check_marker(marker)
        self.write(f":CALCulate:MARKer{marker}:MAXimum")
        x = self._require_data(float(self.query(f":CALCulate:MARKer{marker}:X?")),
                               f"marker {marker} frequency")
        y = self._require_data(float(self.query(f":CALCulate:MARKer{marker}:Y?")),
                               f"marker {marker} amplitude")
        return Reading("peak", y, "dBm", x)

    def marker_at(self, frequency_hz: float, marker: int = 1) -> Reading:
        marker = self._check_marker(marker)
        start = self.center_hz - self.span_hz / 2
        stop = self.center_hz + self.span_hz / 2
        requested_hz = self._check(f"marker {marker} frequency", frequency_hz,
                                   start, stop, " Hz")
        self.write_bool(f":CALCulate:MARKer{marker}:STATe", True)
        self.write(f":CALCulate:MARKer{marker}:X {requested_hz:.3f}")
        x = float(self.query(f":CALCulate:MARKer{marker}:X?"))
        y = self._require_data(float(self.query(f":CALCulate:MARKer{marker}:Y?")),
                               f"marker {marker} amplitude")
        return Reading.at_frequency(requested_hz, y, frequency_hz=x)

    def marker_frequency_counter(self, marker: int = 1, *,
                                 precision: str = "FINE") -> Reading:
        marker = self._check_marker(marker)
        precision_key = str(precision).upper()
        if precision_key not in _FCOUNT_PRECISIONS:
            raise ParameterError(
                f"frequency counter precision must be one of {tuple(_FCOUNT_PRECISIONS)}, "
                f"got {precision!r}")
        self.write_bool(f":CALCulate:MARKer{marker}:STATe", True)
        self.write_bool(f":CALCulate:MARKer{marker}:FCOunt:STATe", True)
        self.write(f":CALCulate:MARKer{marker}:FCOunt:PRECision "
                   f"{_FCOUNT_PRECISIONS[precision_key]}")
        frequency_hz = self._require_data(
            float(self.query(f":CALCulate:MARKer{marker}:FCOunt:X?")),
            f"marker {marker} frequency counter (no countable signal?)")
        return Reading("frequency counter", frequency_hz, "Hz", frequency_hz)

    def peak_frequency(self, marker: int = 1, *, precision: str = "FINE") -> Reading:
        self.peak_search(marker)
        return self.marker_frequency_counter(marker, precision=precision)

    def save_screen_image(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        quoted = f'"{_SCREENSHOT_REMOTE}"'
        self.errors()
        self.write(f":MMEM:STOR:SCR {quoted}")
        self.opc(timeout_s=_SCREENSHOT_TIMEOUT_S)
        try:
            self.check_errors(":MMEM:STOR:SCR")
            with self._binary_read(timeout_s=_SCREENSHOT_TIMEOUT_S):
                data = self._res.query_binary_values(
                    f":MMEM:DATA? {quoted}", datatype="B", is_big_endian=False,
                    container=bytearray, header_fmt="ieee",
                    expect_termination=False)
        finally:
            self.write(f":MMEM:DEL {quoted}")
        payload = bytes(data)
        if not payload.startswith(b"\x89PNG"):
            raise InstrumentError("screenshot transfer did not return a PNG")
        output.write_bytes(payload)
        return output

    def configure(self, *, center_hz: float | None = None,
                  span_hz: float | None = None,
                  rbw_hz: float | None = None, vbw_hz: float | None = None,
                  points: int | None = None, sweep_time_s: float | None = None,
                  ref_level_dbm: float | None = None,
                  attenuation_db: float | None = None,
                  preamp: bool | None = None,
                  detector: str | None = None) -> Settings:
        if center_hz is not None and span_hz is not None:
            self.set_frequency(center_hz, span_hz)
        elif center_hz is not None:
            self.center_hz = center_hz
        elif span_hz is not None:
            self.span_hz = span_hz

        if rbw_hz is not None:
            self.rbw_hz = rbw_hz
        if vbw_hz is not None:
            self.vbw_hz = vbw_hz
        if points is not None:
            self.points = points
        if ref_level_dbm is not None:
            self.ref_level_dbm = ref_level_dbm
        if attenuation_db is not None:
            self.attenuation_db = attenuation_db
        if preamp is not None:
            self.preamp_on = preamp
        if detector is not None:
            self.detector = detector
        if sweep_time_s is not None:
            self.sweep_time_s = sweep_time_s

        self.check_errors("configure")
        return self.settings()

    def _check_marker(self, marker: int) -> int:
        return self._check_int("marker", marker, 1, self.limits.markers)
