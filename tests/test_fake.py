"""Fake instrument behaviour."""

import struct

import numpy as np
import pytest

from rfsa import N9020A
from rfsa.fake import FakeResource, FakeSettings, fake_resource, fake_screenshot_png, synthetic_trace


def test_synthetic_trace_has_noise_floor_and_peak():
    trace = synthetic_trace(1e9, 10e6, 1001, peak_dbm=-20.5, rbw_hz=30e3)
    assert len(trace) == 1001
    assert trace.max() == pytest.approx(-20.5)
    assert np.median(trace) < -80.0
    assert len(np.unique(trace)) > 100


def test_fake_screenshot_is_a_realistic_png():
    trace = synthetic_trace(1e9, 10e6, 1001)
    png = fake_screenshot_png(trace, center_hz=1e9, span_hz=10e6)
    assert png.startswith(b"\x89PNG")
    width, height = struct.unpack(">II", png[16:24])
    assert width >= 400
    assert height >= 200
    assert len(png) > 1000


def test_fake_resource_tracks_configure_and_regenerates_trace():
    resource = fake_resource()
    sa = N9020A(resource)
    sa.configure(center_hz=2e9, span_hz=20e6, rbw_hz=100e3, points=501)
    assert resource.settings.center_hz == 2e9
    assert len(resource.trace) == 501
    assert resource.responses[":SENSe:FREQuency:CENTer?"] == "2000000000.000"
    peak = sa.peak_search()
    assert peak.frequency_hz == pytest.approx(2e9, rel=1e-6)
    assert peak.value == pytest.approx(-20.5)


def test_fake_resource_counter_follows_peak():
    resource = fake_resource()
    sa = N9020A(resource)
    sa.configure(center_hz=1e9, span_hz=10e6, rbw_hz=30e3, points=1001)
    sa.peak_search()
    counter = sa.marker_frequency_counter()
    assert counter.value == pytest.approx(1e9 + 123.456)


def test_manual_tone_trace_still_works_for_simple_tests():
    settings = FakeSettings(center_hz=1e9, span_hz=10e6, rbw_hz=30e3, points=101)
    resource = FakeResource(
        trace=synthetic_trace(1e9, 10e6, 101, seed=1),
        settings=settings,
    )
    sa = N9020A(resource)
    sweep = sa.capture()
    assert len(sweep) == 101
    assert sweep.peak.value == pytest.approx(-20.5)
