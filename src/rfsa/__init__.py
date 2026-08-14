"""rfsa — automated spectrum measurements on a Keysight MXA N9020A."""

from .analyzer import N9020A, connect, is_invalid_scpi_value, open_visa
from .errors import (ConnectionFailed, InstrumentError, ParameterError, RfsaError,
                     ScpiError)
from .limits import Limits
from .models import Identity, Reading, Settings, Sweep
from .storage import Storage

__version__ = "0.2.0"

__all__ = ["N9020A", "connect", "open_visa", "Storage", "Limits",
           "Identity", "Settings", "Sweep", "Reading",
           "RfsaError", "ConnectionFailed", "InstrumentError", "ScpiError",
           "ParameterError", "is_invalid_scpi_value", "__version__"]
