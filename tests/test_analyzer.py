"""Driver tests."""

import numpy as np
import pytest

from rfsa import (Identity, InstrumentError, Limits, N9020A, ParameterError,
                  ScpiError)
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
        analyzer = N9020A(resource)
        analyzer.close()
        assert ":INITiate:CONTinuous 1" in resource.writes
        assert resource.closed

    def test_the_transfer_format_is_read_not_assumed(self, resource):
        resource.responses[":FORMat:DATA?"] = "ASC,8"
        assert N9020A(resource).data_format == "ASC"

    def test_the_byte_order_is_read_not_assumed(self, resource):
        resource.responses[":FORMat:BORDer?"] = "NORM"
        assert N9020A(resource).byte_order == "NORM"

    def test_prepare_records_the_byte_order_it_selected(self, resource):
        resource.responses[":FORMat:BORDer?"] = "NORM"
        analyzer = N9020A(resource)
        analyzer.prepare()
        assert analyzer.byte_order == "SWAP"


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

    def test_a_window_reaching_past_the_top_of_the_band_is_refused(self, analyzer,
                                                                  resource):
        """26.4 GHz and 1 GHz are each legal; together they ask for 26.9 GHz."""
        before = len(resource.writes)
        with pytest.raises(ParameterError, match="above the"):
            analyzer.set_frequency(26.4e9, 1e9)
        assert len(resource.writes) == before

    def test_a_window_inside_the_band_is_accepted(self, analyzer):
        analyzer.set_frequency(26.0e9, 1e9)
        assert analyzer.center_hz == pytest.approx(26.0e9)


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

    def test_wait_sweep_does_not_hold_the_analyzer(self, analyzer, resource):
        resource.writes.clear()
        analyzer.wait_sweep()
        assert resource.writes == [":INITiate:IMMediate"]
        assert ":INITiate:CONTinuous 0" not in resource.writes

    def test_hold_freezes_without_starting_a_new_sweep(self, analyzer, resource):
        resource.writes.clear()
        analyzer.hold()
        assert resource.writes == [":INITiate:CONTinuous 0"]
        assert ":INITiate:IMMediate" not in resource.writes

    def test_continuous_sweep_turns_free_run_back_on(self, analyzer, resource):
        resource.writes.clear()
        analyzer.continuous_sweep()
        assert resource.writes == [":INITiate:CONTinuous 1"]

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

    def test_binary_trace_read_disables_the_termination_character(self, analyzer,
                                                                  resource):
        analyzer.read_trace()
        assert resource.binary_reads[-1]["read_termination"] is None
        assert resource.read_termination == "\n", "must be restored afterwards"

    @pytest.mark.parametrize("border, big_endian",
                             [("SWAP", False), ("NORM", True)])
    def test_binary_trace_read_follows_the_instrument_byte_order(
            self, resource, border, big_endian):
        resource.responses[":FORMat:BORDer?"] = border
        N9020A(resource).read_trace()
        assert resource.binary_reads[-1]["is_big_endian"] is big_endian

    def test_an_ascii_trace_is_parsed(self, resource):
        resource.responses[":FORMat:DATA?"] = "ASC,8"
        resource.trace = np.linspace(-90.0, -80.0, 1001)
        sweep = N9020A(resource).read_trace()
        assert len(sweep) == 1001
        assert sweep.amplitudes_dbm[0] == pytest.approx(-90.0)
        assert sweep.amplitudes_dbm[-1] == pytest.approx(-80.0)

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
        assert reading.frequency_hz == pytest.approx(analyzer.center_hz)
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
        reading = analyzer.marker_frequency_counter()
        assert ":CALCulate:MARKer1:FCOunt:STATe 1" in resource.writes
        assert reading.value == pytest.approx(analyzer.center_hz + 123.456)
        assert reading.unit == "Hz"

    def test_counter_only_sends_commands_the_n9020a_defines(self, analyzer, resource):
        analyzer.marker_frequency_counter()
        assert ":CALCulate:MARKer1:FCOunt:RESolution:AUTO 1" in resource.writes
        assert not [c for c in resource.writes if "PRECision" in c]

    def test_counter_retries_until_the_gate_time_closes(self, analyzer, resource):
        analyzer.fcount_settle_s = 0
        answers = ["9.91e37", "9.91e37", "1000000123.456"]
        resource.responses[":CALCulate:MARKer1:FCOunt:X?"] = answers[0]

        real_query = resource.query

        def query(command):
            if command == ":CALCulate:MARKer1:FCOunt:X?" and answers:
                resource.responses[command] = answers.pop(0)
            return real_query(command)

        resource.query = query
        reading = analyzer.marker_frequency_counter()
        assert reading.value == pytest.approx(1000000123.456)

    def test_keysight_nodata_sentinel_is_detected(self):
        assert is_invalid_scpi_value(9.91e37)
        assert not is_invalid_scpi_value(1e9)

    def test_an_uncountable_signal_is_refused_not_returned(self, analyzer, resource):
        analyzer.fcount_settle_s = 0
        resource.responses[":CALCulate:MARKer1:FCOunt:X?"] = "9.91e37"
        with pytest.raises(InstrumentError, match="9.91e37"):
            analyzer.marker_frequency_counter()

    def test_a_marker_with_no_data_is_refused(self, analyzer, resource):
        resource.responses[":CALCulate:MARKer1:Y?"] = "9.91e37"
        with pytest.raises(InstrumentError, match="9.91e37"):
            analyzer.peak_search()

    def test_peak_frequency_searches_before_counter(self, analyzer, resource):
        analyzer.peak_frequency()
        peak = resource.writes.index(":CALCulate:MARKer1:MAXimum")
        counter = resource.writes.index(":CALCulate:MARKer1:FCOunt:STATe 1")
        assert peak < counter

    def test_counter_sweeps_again_when_the_analyzer_is_held(self, analyzer, resource):
        analyzer.single_sweep()
        resource.writes.clear()
        analyzer.marker_frequency_counter()
        assert ":INITiate:IMMediate" in resource.writes

    def test_counter_does_not_start_a_sweep_while_free_running(self, analyzer, resource):
        resource.writes.clear()
        analyzer.marker_frequency_counter()
        assert ":INITiate:IMMediate" not in resource.writes


class TestScreenCapture:
    def test_save_screen_image_transfers_png(self, analyzer, resource, tmp_path):
        image = analyzer.save_screen_image(tmp_path / "screen.png")
        assert image.read_bytes().startswith(b"\x89PNG")
        quoted = r'"D:\rfsa_screen.png"'
        assert f":MMEM:STOR:SCR {quoted}" in resource.writes
        assert f":MMEM:DATA? {quoted}" in resource.queries
        assert f":MMEM:DEL {quoted}" in resource.writes
        assert resource.files == {}

    def test_screenshot_read_disables_termination_and_restores_the_timeout(
            self, analyzer, resource, tmp_path):
        analyzer.save_screen_image(tmp_path / "screen.png")
        read = resource.binary_reads[-1]
        assert read["command"].startswith(":MMEM:DATA?")
        assert read["read_termination"] is None
        assert read["timeout"] == 30000, "screenshots need a longer timeout"
        assert resource.read_termination == "\n"
        assert resource.timeout == 10000

    def test_stale_error_queue_is_drained_before_capture(
            self, analyzer, resource, tmp_path):
        resource.error_queue = ['-113,"Undefined header"', '+0,"No error"']
        image = analyzer.save_screen_image(tmp_path / "screen.png")
        assert image.read_bytes().startswith(b"\x89PNG")


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
