"""Fixtures.

Unit tests run against :class:`~tests.fake.FakeResource` — no instrument, no
network. Tests marked ``@pytest.mark.hardware`` run against a real analyzer and
are skipped unless you pass one::

    pytest --visa=TCPIP0::192.168.0.10::inst0::INSTR
"""

from __future__ import annotations

import pytest

from rfsa import N9020A, Storage, connect

from rfsa.fake import FakeResource


def pytest_addoption(parser):
    parser.addoption("--visa", action="store", default=None, metavar="RESOURCE",
                     help="VISA address of a real N9020A; enables hardware tests")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--visa"):
        return
    skip = pytest.mark.skip(reason="needs --visa=<resource>")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def resource() -> FakeResource:
    return FakeResource()


@pytest.fixture
def analyzer(request, resource):
    """The driver. Real hardware only for tests marked ``hardware``."""
    address = request.config.getoption("--visa")
    if address and request.node.get_closest_marker("hardware"):
        # No preset(): *RST would wipe the settings of whoever is using the
        # analyzer. Hardware tests configure what they need and nothing else.
        instrument = connect(address)
    else:
        instrument = N9020A(resource)
    yield instrument
    instrument.close()


@pytest.fixture
def db() -> Storage:
    with Storage(":memory:") as storage:
        yield storage
