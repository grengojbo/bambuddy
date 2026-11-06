"""Scale recovery: a load cell that is not ready at boot must be picked up later.

On a Pi without an RTC the daemon starts seconds after power-on. An HX711 that
has not finished its first conversion by then fails `init()`, and the daemon
used to give up on the scale until the service was restarted by hand.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from daemon import main as daemon_main, scale_reader
from daemon.config import Config
from daemon.main import _scale_diag_script, scale_poll_loop

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _config():
    return Config(
        backend_url="http://localhost:5000",
        api_key="test-key",
        device_id="dev-1",
        hostname="test-host",
        scale_read_interval=0.0,
        scale_report_interval=0.0,
    )


async def _inline_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


async def _spin(task, until, limit=200):
    for _ in range(limit):
        if until():
            break
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestScaleReaderReinit:
    def test_failed_boot_init_recovers_on_reinit(self):
        first = MagicMock()
        first.init.side_effect = TimeoutError("DOUT never went low")
        second = MagicMock()
        with patch("daemon.scale_reader._open_driver", side_effect=[first, second]) as factory:
            reader = scale_reader.ScaleReader(driver="hx711")
            assert reader.ok is False

            assert reader.reinit() is True

        assert reader.ok is True
        assert [c.args for c in factory.call_args_list] == [("hx711",), ("hx711",)]
        first.close.assert_called_once()
        second.init.assert_called_once()

    def test_reinit_failure_keeps_scale_unavailable(self):
        broken = MagicMock()
        broken.init.side_effect = TimeoutError("still nothing")
        with patch("daemon.scale_reader._open_driver", return_value=broken):
            reader = scale_reader.ScaleReader(driver="hx711")
            assert reader.reinit() is False
        assert reader.ok is False

    def test_reinit_keeps_calibration(self):
        broken = MagicMock()
        broken.init.side_effect = TimeoutError
        working = MagicMock()
        working.data_ready.return_value = True
        working.read_raw.return_value = 1100
        with patch("daemon.scale_reader._open_driver", side_effect=[broken, working]):
            reader = scale_reader.ScaleReader(tare_offset=1000, calibration_factor=0.5, driver="hx711")
            reader.reinit()
        grams, _stable, raw = reader.read()
        assert raw == 1100
        assert grams == 50.0


class TestScalePollLoopRetry:
    @pytest.mark.asyncio
    async def test_unavailable_scale_is_retried_until_it_comes_up(self):
        scale = MagicMock()
        scale.ok = False
        attempts = []

        def _reinit():
            attempts.append(1)
            if len(attempts) >= 3:
                scale.ok = True
            return scale.ok

        scale.reinit.side_effect = _reinit
        scale.read.return_value = (12.0, True, 4242)
        api = AsyncMock()
        shared = {"scale": scale, "display": MagicMock()}

        with (
            patch("daemon.main.asyncio.to_thread", _inline_to_thread),
            patch.object(daemon_main, "SCALE_RETRY_MIN_S", 0.0),
            patch.object(daemon_main, "SCALE_RETRY_MAX_S", 0.0),
        ):
            task = asyncio.create_task(scale_poll_loop(_config(), api, shared))
            await _spin(task, lambda: api.scale_reading.await_count > 0)

        assert len(attempts) == 3
        api.scale_reading.assert_awaited()
        assert api.scale_reading.await_args.kwargs["raw_adc"] == 4242

    @pytest.mark.asyncio
    async def test_loop_does_not_exit_while_scale_is_missing(self):
        scale = MagicMock()
        scale.ok = False
        scale.reinit.return_value = False
        shared = {"scale": scale, "display": MagicMock()}

        with (
            patch("daemon.main.asyncio.to_thread", _inline_to_thread),
            patch.object(daemon_main, "SCALE_RETRY_MIN_S", 0.0),
            patch.object(daemon_main, "SCALE_RETRY_MAX_S", 0.0),
        ):
            task = asyncio.create_task(scale_poll_loop(_config(), AsyncMock(), shared))
            await _spin(task, lambda: scale.reinit.call_count >= 5)

        assert scale.reinit.call_count >= 5
        scale.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_reads_while_a_diagnostic_holds_the_scale(self):
        scale = MagicMock()
        scale.ok = True
        scale.read.return_value = None
        lock = asyncio.Lock()
        shared = {"scale": scale, "display": MagicMock(), "scale_lock": lock}

        await lock.acquire()
        with patch("daemon.main.asyncio.to_thread", _inline_to_thread):
            task = asyncio.create_task(scale_poll_loop(_config(), AsyncMock(), shared))
            for _ in range(20):
                await asyncio.sleep(0)
            scale.read.assert_not_called()

            lock.release()
            await _spin(task, lambda: scale.read.call_count > 0)

        assert scale.read.call_count > 0


class TestScaleDiagnosticScript:
    def test_hx711_runs_the_hx711_diagnostic(self):
        assert _scale_diag_script("hx711") == "hx711_diag.py"

    @pytest.mark.parametrize("driver", ["nau7802", "", None])
    def test_stock_hardware_runs_the_nau7802_diagnostic(self, driver):
        assert _scale_diag_script(driver) == "scale_diag.py"

    @pytest.mark.parametrize("driver", ["hx711", "nau7802"])
    def test_selected_script_exists(self, driver):
        assert (SCRIPTS_DIR / _scale_diag_script(driver)).is_file()
