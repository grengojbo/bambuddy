"""HX711 24-bit load cell ADC driver — alternative to the NAU7802.

The HX711 has no bus: data comes out on DOUT while the host clocks PD_SCK, and
the chip powers itself down if PD_SCK is held high for more than 60 µs. That
deadline is the whole design constraint here.

Character-device GPIO cannot meet it from Python. Measured on a Pi Model B+
(ARMv6, 700 MHz), 200 bursts of 24 pulses, time spent with PD_SCK high:

    lgpio      median 130 µs   100% over the 60 µs limit
    gpiod v2   median 535 µs   100% over
    mmap        median 14 µs   0.12% over (with SCHED_FIFO)

So this driver maps the GPIO block directly via /dev/gpiomem and keeps the high
phase down to two register writes, sampling DOUT afterwards while PD_SCK is low.
That is both correct — the bit settles on the falling edge and holds until the
next rising edge — and twice as fast as sampling inside the high phase.

Reads that do overrun are caught two ways. Most leave DOUT low where a finished
conversion would have raised it, and are discarded outright. The rest come from
a chip that power-cycled and completed a fresh conversion, so the garbage looks
well-formed — those are caught by confirming any sudden jump with a second read,
since corruption is uncorrelated between reads while real weight changes persist.
Both matter in practice: NFC polling shares the same core, and one wild sample
poisons ScaleReader's moving average for twenty readings.

No extra dependencies: mmap is in the standard library, and /dev/gpiomem is
readable by the `gpio` group, so no root either.
"""

import gc
import logging
import mmap
import os
import statistics
import time

from .hw_util import env_int

logger = logging.getLogger(__name__)

DT_PIN = env_int("SPOOLBUDDY_SCALE_DT_PIN", 5)
SCK_PIN = env_int("SPOOLBUDDY_SCALE_SCK_PIN", 6)
GAIN = env_int("SPOOLBUDDY_SCALE_GAIN", 128)
# The module converts at 10 SPS, so every extra sample divides the effective
# rate. One is enough because a mistimed read is *detected* and retried rather
# than silently wrong, and ScaleReader already averages over 20 readings —
# whose stability window needs at least 3 readings per second to work. Raise it
# only if a particular cell turns out noisy.
SAMPLES = env_int("SPOOLBUDDY_SCALE_SAMPLES", 1)
# A change larger than this is confirmed by a second read before it is
# believed. ~240 g on a 5 kg cell: far above the noise floor, far below a
# spool being put down.
JUMP_THRESHOLD = env_int("SPOOLBUDDY_SCALE_JUMP_THRESHOLD", 100_000)
RT_PRIO = env_int("SPOOLBUDDY_SCALE_RT_PRIO", 50)

GPIOMEM = "/dev/gpiomem"

# BCM283x GPIO register offsets, in 32-bit words from the start of the block
W_GPFSEL0 = 0
W_GPSET0 = 7
W_GPCLR0 = 10
W_GPLEV0 = 13

# Extra pulses after the 24 data bits select the channel and gain of the *next*
# conversion (datasheet table 3).
GAIN_PULSES = {128: 1, 32: 2, 64: 3}

SATURATED = (0x7FFFFF, -0x800000)
STATUS_LOG_INTERVAL = 60.0
MAX_RETRIES = 4


class HX711:
    def __init__(self, dt_pin: int = DT_PIN, sck_pin: int = SCK_PIN, gain: int = GAIN):
        if gain not in GAIN_PULSES:
            raise ValueError(f"Unsupported gain {gain}, expected one of {sorted(GAIN_PULSES)}")
        self._dt_pin = dt_pin
        self._sck_pin = sck_pin
        self._gain = gain
        self._gain_pulses = GAIN_PULSES[gain]
        self._dt_bit = 1 << dt_pin
        self._sck_bit = 1 << sck_pin

        if dt_pin > 9 or sck_pin > 9:
            # GPFSEL0 covers GPIO0-9; supporting higher lines means selecting the
            # right GPFSEL register, which the default pinout never needs.
            raise ValueError("HX711 pins must be GPIO0-9 (defaults: DT=GPIO5, SCK=GPIO6)")

        fd = os.open(GPIOMEM, os.O_RDWR | os.O_SYNC)
        try:
            self._mem = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0)
        finally:
            os.close(fd)
        self._reg = memoryview(self._mem).cast("I")

        self._discarded = 0
        self._reads = 0
        self._last_accepted: int | None = None
        self._last_status_log = 0.0
        self._rt_applied = False

    def describe(self) -> str:
        return f"HX711 on GPIO{self._dt_pin}/GPIO{self._sck_pin} via {GPIOMEM}, gain {self._gain}"

    def init(self):
        """Configure the lines, leave power-down, and drop startup transients."""
        fsel = self._reg[W_GPFSEL0]
        fsel &= ~(0b111 << (self._sck_pin * 3))
        fsel |= 0b001 << (self._sck_pin * 3)  # SCK: output
        fsel &= ~(0b111 << (self._dt_pin * 3))  # DT: input
        self._reg[W_GPFSEL0] = fsel

        self._reg[W_GPCLR0] = self._sck_bit  # a low PD_SCK leaves power-down
        time.sleep(0.5)

        self._apply_rt_priority()

        if not self.wait_data_ready(timeout_s=2.0):
            raise TimeoutError("HX711 not responding — DOUT never went low (check power, DT wiring)")

        self.flush_readings(count=3)
        logger.debug("HX711 initialised: %s", self.describe())

    def _apply_rt_priority(self):
        """Ask for real-time scheduling; harmless and optional if refused.

        Cuts the tail of preempted reads roughly in half. Needs CAP_SYS_NICE,
        which the systemd unit grants.
        """
        if RT_PRIO <= 0 or self._rt_applied:
            return
        try:
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(RT_PRIO))
            self._rt_applied = True
            logger.debug("HX711 reader running at SCHED_FIFO %d", RT_PRIO)
        except (PermissionError, OSError) as e:
            logger.info("SCHED_FIFO unavailable (%s) — timing margin is smaller but usable", e)

    def close(self):
        try:
            self._reg[W_GPCLR0] = self._sck_bit
        except Exception:
            pass
        try:
            self._mem.close()
        except Exception:
            pass

    # -- reading --

    def data_ready(self) -> bool:
        """DOUT low means a conversion is ready to be clocked out."""
        return not (self._reg[W_GPLEV0] & self._dt_bit)

    def wait_data_ready(self, timeout_s: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.data_ready():
                return True
            time.sleep(0.001)
        return False

    def flush_readings(self, count: int = 3, timeout_s: float = 1.0) -> None:
        for _ in range(count):
            if not self.wait_data_ready(timeout_s=timeout_s):
                raise TimeoutError("Timeout while flushing startup scale readings")
            self._read_once()

    def _read_once(self) -> int | None:
        """Clock out one conversion. Returns None if the read was corrupted.

        Everything between disabling and restoring the collector is timing
        critical: a GC pause here holds PD_SCK high past the 60 µs limit.
        """
        reg = self._reg
        sck, dt = self._sck_bit, self._dt_bit

        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            value = 0
            for _ in range(24):
                reg[W_GPSET0] = sck
                reg[W_GPCLR0] = sck
                # Sample after the falling edge: the bit this pulse shifted out
                # is valid from here until the next rising edge. Sampling before
                # the pulse instead reads the ready state as the MSB and drops
                # the LSB, which halves every reading and destroys the sign bit.
                value = (value << 1) | (1 if reg[W_GPLEV0] & dt else 0)
            for _ in range(self._gain_pulses):
                reg[W_GPSET0] = sck
                reg[W_GPCLR0] = sck
        finally:
            if gc_was_enabled:
                gc.enable()
            reg[W_GPCLR0] = sck

        if not (reg[W_GPLEV0] & dt):
            # DOUT should have gone high once the conversion was clocked out;
            # still low means the chip power-cycled mid-read.
            return None

        if value & 0x800000:
            value -= 0x1000000
        if value in SATURATED:
            return None
        return value

    def _read_valid(self) -> int:
        """One conversion, retrying past reads the timing check rejected."""
        for _ in range(MAX_RETRIES):
            if not self.wait_data_ready(timeout_s=0.5):
                break
            value = self._read_once()
            if value is not None:
                return value
            self._discarded += 1
        raise TimeoutError("HX711 produced no valid reading")

    def _read_samples(self) -> int:
        if SAMPLES <= 1:
            return self._read_valid()
        return int(statistics.median([self._read_valid() for _ in range(SAMPLES)]))

    def read_raw(self) -> int:
        """One conversion, with a sudden jump confirmed before it is believed.

        A power-down mid-read usually leaves DOUT low and is caught in
        `_read_once()`, but not always: the chip restarts, finishes a fresh
        conversion, and raises DOUT anyway, so the garbage looks well-formed.
        Under load — NFC polling competes for the same core — that happens often
        enough to matter, and a single wild sample poisons ScaleReader's moving
        average for the next twenty readings.

        Corruption is uncorrelated between reads while a real weight change
        persists, so a second read settles which one this is. Steady state costs
        nothing: the extra read only happens when the value actually jumps.

        Raises TimeoutError if nothing usable arrives, which ScaleReader treats
        as a transient failure.
        """
        value = self._read_samples()

        if self._last_accepted is not None and abs(value - self._last_accepted) > JUMP_THRESHOLD:
            second = self._read_samples()
            if abs(second - value) > JUMP_THRESHOLD:
                # The two disagree, so at least one is garbage. A third read
                # gives a majority — the median is the one that is not.
                self._discarded += 1
                value = int(statistics.median((value, second, self._read_samples())))
            else:
                value = second

        self._reads += 1
        self._last_accepted = value
        self._log_status()
        return value

    def _log_status(self):
        now = time.monotonic()
        if now - self._last_status_log < STATUS_LOG_INTERVAL:
            return
        self._last_status_log = now
        if self._discarded:
            logger.info(
                "HX711: %d reads, %d samples discarded on timing (%.2f%%)",
                self._reads,
                self._discarded,
                100.0 * self._discarded / max(self._reads * SAMPLES, 1),
            )
