"""Driver tests."""

import numpy as np
import pytest

from rfsa import Identity, Limits, N9020A, ParameterError, ScpiError
from rfsa.analyzer import is_invalid_scpi_value
from rfsa.fake import FakeResource


class TestSession:
    def test_identity_is_read_on_connect(self, analyzer):
        assert analyzer.identity.model == "N9020A"
        assert analyzer.identity.serial == "MY49010001"

    def test_identity_parsing_tolerates_a_short_response(self):
        assert Identity.parse("Keysight,N9020A") == Identity("Keysight", "N9020A", "", "")

    def test_another_model_is_refused(self):
        resource = FakeResource({"*IDN?": "Keysight,N9010A,MY1,A.1"})
        with pytest.raises(Exception, match="N9010A"):
            N9020A(resource)

    def test_connecting_changes_nothing_on_the_instrument(self, resource):
        N9020A(resource)
        assert resource.writes == []

    def test_prepare_is_opt_in(self, resource):
        N9020A(resource, prepare=True)
        assert ":INSTrument:SELect SA" in resource.writes
        assert ":FORMat:DATA REAL,64" in resource.writes
        assert ":FORMat:BORDer SWAP" in resource.writes

    def test_close_closes_the_resource(self, resource):
        N9020A(resource).close()
        assert resource.closed

    def test_the_transfer_format_is_read_not_assumed(self, resource):
        resource.responses[":FORMat:DATA?"] = "ASC,8"
        assert N9020A(resource).data_format == "ASC"


class TestLimits:
    def test_defaults_match_the_lab_instrument(self):
        limits = Limits()
        assert limits.freq_max_hz == 26.5e9
        assert limits.has_preamp is True
        assert limits.points_max == 40001

    def test_sweep_time_range_depends_on_zero_span(self):
        limits = Limits()
        assert limits.sweep_time_range(10e6) == (1e-3, 4000.0)
        assert limits.sweep_time_range(0) == (1e-6, 6000.0)


class TestFrequency:
    def test_center_frequency_command(self, analyzer, resource):
        analyzer.center_hz = 2.4e9
        assert resource.last(":SENSe:FREQuency") == ":SENSe:FREQuency:CENTer 2400000000.000"

    def test_center_frequency_reads_back(self, analyzer):
        analyzer.center_hz = 1e9
        assert analyzer.center_hz == pytest.approx(1e9)

    @pytest.mark.parametrize("value", [30e9, 0.0, 5.0, -1e6, float("nan")])
    def test_out_of_range_never_reaches_the_analyzer(self, analyzer, resource, value):
        before = len(resource.writes)
        with pytest.raises(ParameterError):
            analyzer.center_hz = value
        assert len(resource.writes) == before

    def test_set_frequency_writes_span_before_center(self, analyzer, resource):
        resource.writes.clear()
        analyzer.set_frequency(1e9, 10e6)
        order = [c.split()[0] for c in resource.writes]
        assert order == [":SENSe:FREQuency:SPAN", ":SENSe:FREQuency:CENTer"]

    def test_zero_span_is_allowed_but_1_hz_is_not(self, analyzer):
        analyzer.span_hz = 0
        with pytest.raises(ParameterError, match="span"):
            analyzer.span_hz = 1.0


class TestBandwidthAndAmplitude:
    def test_rbw_command(self, analyzer, resource):
        analyzer.rbw_hz = 30e3
        assert resource.last(":SENSe:BANDwidth:RESolution") == \
            ":SENSe:BANDwidth:RESolution 30000"

    def test_attenuation_must_sit_on_the_2_db_grid(self, analyzer, resource):
        analyzer.attenuation_db = 20
        with pytest.raises(ParameterError, match="multiple of 2"):
            analyzer.attenuation_db = 15

    def test_reference_level_range(self, analyzer):
        with pytest.raises(ParameterError, match="reference level"):
            analyzer.ref_level_dbm = 40


class TestSweep:
    def test_points_range(self, analyzer):
        analyzer.points = 401
        with pytest.raises(ParameterError):
            analyzer.points = 40002

    def test_sweep_time_limit_depends_on_span(self, analyzer, resource):
        with pytest.raises(ParameterError, match="sweep time"):
            analyzer.sweep_time_s = 100e-6
        resource.responses[":SENSe:FREQuency:SPAN?"] = "0"
        analyzer.sweep_time_s = 100e-6

    def test_single_sweep_sequence(self, analyzer, resource):
        resource.writes.clear()
        analyzer.single_sweep()
        assert resource.writes == [":INITiate:CONTinuous 0", ":INITiate:IMMediate"]
        assert "*OPC?" in resource.queries

    def test_detector_is_checked(self, analyzer):
        analyzer.detector = "RMS"
        with pytest.raises(ParameterError):
            analyzer.detector = "XYZ"


class TestData:
    def test_settings_are_read_back(self, analyzer):
        settings = analyzer.settings()
        assert settings.center_hz == 13.25e9
        assert settings.points == 1001
        assert settings.preamp is False

    def test_read_trace_returns_amplitudes_and_a_frequency_axis(self, analyzer):
        sweep = analyzer.read_trace()
        assert len(sweep) == 1001
        assert sweep.frequencies_hz[0] == pytest.approx(sweep.settings.start_hz)
        assert sweep.frequencies_hz[-1] == pytest.approx(sweep.settings.stop_hz)

    def test_a_short_trace_is_an_error(self, resource):
        resource.trace = np.zeros(500)
        analyzer = N9020A(resource)
        with pytest.raises(Exception, match="500 points"):
            analyzer.read_trace()

    def test_peak_of_a_captured_sweep(self, resource):
        resource.trace = np.full(1001, -95.0)
        resource.trace[500] = -20.0
        sweep = N9020A(resource).capture(label="tone")
        assert sweep.peak.value == pytest.approx(-20.0)
        assert sweep.label == "tone"


class TestMarkers:
    def test_peak_search(self, analyzer, resource):
        reading = analyzer.peak_search()
        assert ":CALCulate:MARKer1:MAXimum" in resource.writes
        assert reading.frequency_hz == pytest.approx(1e9)
        assert reading.value == pytest.approx(-20.5)

    def test_marker_at_a_frequency(self, analyzer, resource):
        resource.responses[":SENSe:FREQuency:CENTer?"] = "1000000000"
        resource.responses[":SENSe:FREQuency:SPAN?"] = "10000000"
        analyzer.marker_at(1e9)
        assert ":CALCulate:MARKer1:X 1000000000.000" in resource.writes

    def test_marker_outside_the_sweep_is_refused(self, analyzer, resource):
        resource.responses[":SENSe:FREQuency:CENTer?"] = "1000000000"
        resource.responses[":SENSe:FREQuency:SPAN?"] = "10000000"
        with pytest.raises(ParameterError, match="marker 1 frequency"):
            analyzer.marker_at(2e9)

    def test_marker_frequency_counter(self, analyzer, resource):
        reading = analyzer.marker_frequency_counter(precision="fine")
        assert ":CALCulate:MARKer1:FCOunt:STATe 1" in resource.writes
        assert reading.value == pytest.approx(1000000123.456)
        assert reading.unit == "Hz"

    def test_keysight_nodata_sentinel_is_detected(self):
        assert is_invalid_scpi_value(9.91e37)
        assert not is_invalid_scpi_value(1e9)

    def test_peak_frequency_searches_before_counter(self, analyzer, resource):
        analyzer.peak_frequency()
        peak = resource.writes.index(":CALCulate:MARKer1:MAXimum")
        counter = resource.writes.index(":CALCulate:MARKer1:FCOunt:STATe 1")
        assert peak < counter


class TestScreenCapture:
    def test_save_screen_image_transfers_png(self, analyzer, resource, tmp_path):
        image = analyzer.save_screen_image(tmp_path / "screen.png")
        assert image.read_bytes().startswith(b"\x89PNG")
        assert ":HCOPy:SDUMp:DATA:FTYPe PNG" in resource.writes
        assert ":HCOPy:SDUMp:DATA?" in resource.queries


class TestErrors:
    def test_error_queue_is_drained_and_raised(self, analyzer, resource):
        resource.error_queue = ['-113,"Undefined header"', '+0,"No error"']
        with pytest.raises(ScpiError) as excinfo:
            analyzer.check_errors(":BOGUS")
        assert excinfo.value.code == -113


class TestConfigure:
    def test_applies_and_reads_back(self, analyzer, resource):
        settings = analyzer.configure(center_hz=1e9, span_hz=10e6, rbw_hz=30e3,
                                      ref_level_dbm=-10, attenuation_db=10,
                                      points=1001, detector="RMS")
        assert ":SENSe:FREQuency:CENTer 1000000000.000" in resource.writes
        assert settings.rbw_hz == 30e3


@pytest.mark.hardware
def test_capture_on_real_hardware(analyzer):
    analyzer.configure(center_hz=1e9, span_hz=10e6, rbw_hz=30e3, attenuation_db=10)
    sweep = analyzer.capture()
    assert len(sweep) == sweep.settings.points
    assert np.isfinite(sweep.amplitudes_dbm).all()
