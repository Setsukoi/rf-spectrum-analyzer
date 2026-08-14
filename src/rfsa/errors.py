"""Exceptions."""

from __future__ import annotations


class RfsaError(Exception):
    """Base class for everything this package raises."""


class ConnectionFailed(RfsaError):
    """The VISA resource could not be opened."""


class InstrumentError(RfsaError):
    """The instrument answered, but not the way it should have."""


class ScpiError(InstrumentError):
    """An entry popped from the instrument's ``SYSTem:ERRor?`` queue."""

    def __init__(self, code: int, message: str, command: str | None = None):
        self.code = code
        self.message = message
        self.command = command
        text = f"[{code}] {message}"
        if command:
            text += f" (after {command!r})"
        super().__init__(text)


class ParameterError(RfsaError, ValueError):
    """A setting was rejected locally, before it reached the instrument.

    The N9020A clips an out-of-range value to its nearest limit and carries on
    with a silently wrong setup, so the driver checks first.
    """
