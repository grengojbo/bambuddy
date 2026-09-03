"""Scale reader wrapper with stability detection and calibration."""

import logging
import os
import time
from collections import deque

logger = logging.getLogger(__name__)

MOVING_AVG_SIZE = 20


def _describe(scale) -> str:
    """Human-readable summary of the active driver for the startup log."""
    describe = getattr(scale, "describe", None)
    if callable(describe):
        return describe()
    return f"NAU7802 on I2C bus {getattr(scale, '_bus_num', '?')}"


def _open_driver(driver: str):
    """Instantiate the configured load cell ADC."""
    if driver == "hx711":
        from .hx711 import HX711

        return HX711()

    from .nau7802 import NAU7802

    return NAU7802()


class ScaleReader:
    def __init__(self, tare_offset: int = 0, calibration_factor: float = 1.0, driver: str | None = None):
        self._scale = None
        self._tare_offset = tare_offset
        self._calibration_factor = calibration_factor
        self._samples: deque[float] = deque(maxlen=MOVING_AVG_SIZE)
        self._stability_history: deque[tuple[float, float]] = deque(maxlen=20)
        self._ok = False
        self._last_raw = 0

        if driver is None:
            driver = os.environ.get("SPOOLBUDDY_SCALE_DRIVER", "nau7802").strip().lower() or "nau7802"

        try:
            self._scale = _open_driver(driver)
            self._scale.init()
            self._ok = True
            logger.info(
                "Scale initialized: %s (tare=%d, cal=%.6f)",
                _describe(self._scale),
                tare_offset,
                calibration_factor,
            )
        except Exception as e:
            logger.info("Scale not available (driver=%s): %s", driver, e)

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def last_raw(self) -> int:
        return self._last_raw

    def close(self):
        try:
            if self._scale:
                self._scale.close()
        except Exception:
            pass

    def update_calibration(self, tare_offset: int, calibration_factor: float):
        self._tare_offset = tare_offset
        self._calibration_factor = calibration_factor
        logger.info("Calibration updated: tare=%d, factor=%.6f", tare_offset, calibration_factor)

    def tare(self):
        """Set current raw reading as tare offset."""
        if self._last_raw:
            self._tare_offset = self._last_raw
            self._samples.clear()
            self._stability_history.clear()
            logger.info("Tared at raw=%d", self._tare_offset)
        return self._tare_offset

    def read(self) -> tuple[float, bool, int] | None:
        """Read current weight. Returns (grams, stable, raw_adc) or None."""
        try:
            if not self._scale.data_ready():
                return None

            raw = self._scale.read_raw()
            self._last_raw = raw
            self._ok = True

            grams = (raw - self._tare_offset) * self._calibration_factor
            self._samples.append(grams)

            # Moving average
            avg_grams = sum(self._samples) / len(self._samples)

            # Stability: track readings over time
            now = time.monotonic()
            self._stability_history.append((now, avg_grams))

            # Stable if all readings within 1s window are within 2g of each other
            stable = False
            if len(self._stability_history) >= 5:
                cutoff = now - 1.0
                recent = [g for t, g in self._stability_history if t >= cutoff]
                if len(recent) >= 3:
                    spread = max(recent) - min(recent)
                    stable = spread < 2.0

            return round(avg_grams, 1), stable, raw

        except Exception as e:
            logger.debug("Scale read error: %s", e)
            self._ok = False
            return None
