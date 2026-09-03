"""Tests for driver selection — Config parsing and the reader/scale factories."""

import types
from unittest.mock import MagicMock, patch

import pytest
from daemon import nfc_reader, scale_reader
from daemon.config import NFC_DRIVERS, SCALE_DRIVERS, Config

REQUIRED = {
    "SPOOLBUDDY_BACKEND_URL": "http://localhost:8000",
    "SPOOLBUDDY_API_KEY": "bb_test",
}


@pytest.fixture
def env(monkeypatch):
    for key in ("SPOOLBUDDY_NFC_DRIVER", "SPOOLBUDDY_SCALE_DRIVER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


class TestConfigDrivers:
    def test_defaults_are_the_stock_hardware(self, env):
        cfg = Config.load()
        assert cfg.nfc_driver == "pn5180"
        assert cfg.scale_driver == "nau7802"

    def test_selects_alternative_hardware(self, env):
        env.setenv("SPOOLBUDDY_NFC_DRIVER", "mfrc522")
        env.setenv("SPOOLBUDDY_SCALE_DRIVER", "hx711")
        cfg = Config.load()
        assert cfg.nfc_driver == "mfrc522"
        assert cfg.scale_driver == "hx711"

    @pytest.mark.parametrize("value", ["MFRC522", "  mfrc522  ", "MfRc522"])
    def test_case_and_whitespace_tolerant(self, env, value):
        env.setenv("SPOOLBUDDY_NFC_DRIVER", value)
        assert Config.load().nfc_driver == "mfrc522"

    def test_empty_falls_back_to_default(self, env):
        env.setenv("SPOOLBUDDY_NFC_DRIVER", "")
        assert Config.load().nfc_driver == "pn5180"

    def test_unknown_nfc_driver_rejected(self, env):
        env.setenv("SPOOLBUDDY_NFC_DRIVER", "pn532")
        with pytest.raises(RuntimeError, match="pn532"):
            Config.load()

    def test_unknown_scale_driver_rejected(self, env):
        env.setenv("SPOOLBUDDY_SCALE_DRIVER", "hx710")
        with pytest.raises(RuntimeError, match="hx710"):
            Config.load()

    def test_error_lists_the_valid_options(self, env):
        env.setenv("SPOOLBUDDY_NFC_DRIVER", "nope")
        with pytest.raises(RuntimeError) as exc:
            Config.load()
        for name in NFC_DRIVERS:
            assert name in str(exc.value)


class TestNFCReaderFactory:
    def test_default_opens_pn5180(self):
        with patch("daemon.pn5180.PN5180") as pn:
            nfc_reader._open_driver("pn5180")
        pn.assert_called_once()

    def test_mfrc522_selected_by_name(self):
        with patch("daemon.mfrc522.MFRC522") as mf:
            nfc_reader._open_driver("mfrc522")
        mf.assert_called_once()

    def test_reads_driver_from_environment(self, monkeypatch):
        monkeypatch.setenv("SPOOLBUDDY_NFC_DRIVER", "mfrc522")
        with patch("daemon.nfc_reader._open_driver") as factory:
            factory.return_value.reader_type = "MFRC522"
            nfc_reader.NFCReader()
        factory.assert_called_once_with("mfrc522")

    def test_explicit_argument_wins_over_environment(self, monkeypatch):
        monkeypatch.setenv("SPOOLBUDDY_NFC_DRIVER", "mfrc522")
        with patch("daemon.nfc_reader._open_driver") as factory:
            nfc_reader.NFCReader(driver="pn5180")
        factory.assert_called_once_with("pn5180")

    def test_hardware_failure_leaves_reader_unavailable(self):
        with patch("daemon.nfc_reader._open_driver", side_effect=RuntimeError("no chip")):
            reader = nfc_reader.NFCReader(driver="mfrc522")
        assert reader.ok is False
        assert reader.reader_type == "Unknown"
        assert reader.connection == "None"

    def test_reports_the_driver_identity(self):
        driver = MagicMock(reader_type="MFRC522", connection="SPI")
        with patch("daemon.nfc_reader._open_driver", return_value=driver):
            reader = nfc_reader.NFCReader(driver="mfrc522")
        assert reader.reader_type == "MFRC522"
        assert reader.connection == "SPI"


class TestPrePollReset:
    def _reader(self, needs_reset):
        driver = MagicMock(needs_reset_before_poll=needs_reset)
        driver.activate_type_a.return_value = None
        with patch("daemon.nfc_reader._open_driver", return_value=driver):
            reader = nfc_reader.NFCReader(driver="x")
        driver.reset.reset_mock()
        return reader, driver

    def test_pn5180_resets_before_each_idle_poll(self):
        reader, driver = self._reader(True)
        reader.poll()
        driver.reset.assert_called_once()

    def test_mfrc522_skips_the_reset(self):
        reader, driver = self._reader(False)
        reader.poll()
        driver.reset.assert_not_called()

    def test_mfrc522_does_not_cycle_rf_while_idle(self):
        """The RF cycle belongs to the tag-present branch, not the idle one."""
        reader, driver = self._reader(False)
        driver.rf_off.reset_mock()
        reader.poll()
        driver.rf_off.assert_not_called()


class TestScaleReaderFactory:
    def test_default_opens_nau7802(self):
        with patch("daemon.nau7802.NAU7802") as nau:
            scale_reader._open_driver("nau7802")
        nau.assert_called_once()

    def test_hx711_selected_by_name(self):
        with patch("daemon.hx711.HX711") as hx:
            scale_reader._open_driver("hx711")
        hx.assert_called_once()

    def test_reads_driver_from_environment(self, monkeypatch):
        monkeypatch.setenv("SPOOLBUDDY_SCALE_DRIVER", "hx711")
        with patch("daemon.scale_reader._open_driver") as factory:
            scale_reader.ScaleReader()
        factory.assert_called_once_with("hx711")

    def test_hardware_failure_leaves_scale_unavailable(self):
        with patch("daemon.scale_reader._open_driver", side_effect=OSError("no gpiomem")):
            scale = scale_reader.ScaleReader()
        assert scale.ok is False

    def test_describe_prefers_the_driver_summary(self):
        driver = MagicMock()
        driver.describe.return_value = "HX711 on GPIO5/GPIO6"
        assert scale_reader._describe(driver) == "HX711 on GPIO5/GPIO6"

    def test_describe_falls_back_for_nau7802(self):
        driver = types.SimpleNamespace(_bus_num=1)
        assert "I2C bus 1" in scale_reader._describe(driver)


class TestDriverListsMatchFactories:
    @pytest.mark.parametrize("name", NFC_DRIVERS)
    def test_every_nfc_driver_is_constructible(self, name):
        with patch("daemon.pn5180.PN5180"), patch("daemon.mfrc522.MFRC522"):
            assert nfc_reader._open_driver(name) is not None

    @pytest.mark.parametrize("name", SCALE_DRIVERS)
    def test_every_scale_driver_is_constructible(self, name):
        with patch("daemon.nau7802.NAU7802"), patch("daemon.hx711.HX711"):
            assert scale_reader._open_driver(name) is not None
