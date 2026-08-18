"""A stand-in for a pyvisa resource — enough to run the driver without hardware."""

from __future__ import annotations

import numpy as np

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\xd9\xcf\x9b\x00\x00\x00\x00IEND\xaeB`\x82"
)

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
    ":CALCulate:MARKer1:X?": "1000000000",
    ":CALCulate:MARKer1:Y?": "-20.5",
    ":CALCulate:MARKer1:FCOunt:X?": "1000000123.456",
}


def tone_trace(points: int = 1001, peak_index: int | None = None,
               peak_dbm: float = -20.0, floor_dbm: float = -95.0) -> np.ndarray:
    amplitudes = np.full(points, floor_dbm, dtype=float)
    amplitudes[points // 2 if peak_index is None else peak_index] = peak_dbm
    return amplitudes


class FakeResource:
    """Duck-types the pyvisa methods the driver uses."""

    def __init__(self, responses: dict | None = None, trace: np.ndarray | None = None,
                 errors: list[str] | None = None):
        self.responses = dict(DEFAULTS)
        self.responses.update(responses or {})
        self.trace = np.linspace(-90.0, -80.0, 1001) if trace is None else trace
        self.files: dict[str, bytes] = {}
        self.error_queue = list(errors or [])
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.binary_reads: list[dict] = []
        self.timeout = 10000
        self.read_termination = "\n"
        self.write_termination = "\n"
        self.closed = False

    def write(self, command: str) -> None:
        self.writes.append(command)
        if command.startswith(":MMEM:STOR:SCR "):
            self.files[self._file_arg(command)] = _TINY_PNG
            return
        if command.startswith(":MMEMory:DELete "):
            self.files.pop(self._file_arg(command), None)
            return
        head, _, argument = command.partition(" ")
        if argument:
            self.responses[head + "?"] = argument.strip()
            if "SWEep:POINts" in head:
                n = int(float(argument))
                if n > 0 and n != len(self.trace):
                    old = np.linspace(0.0, 1.0, len(self.trace))
                    new = np.linspace(0.0, 1.0, n)
                    self.trace = np.interp(new, old, self.trace)

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
            return container(_TINY_PNG)
        if command.startswith(":MMEMory:DATA? "):
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
