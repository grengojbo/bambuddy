"""Tests for daemon.hx711 — bit shifting, corruption detection, median.

The HX711 is simulated at the register level so the tests exercise the real
read loop rather than a stubbed one. Getting the clock phase wrong halves every
reading and destroys the sign bit, so that is what `FakeRegisters` models most
carefully: DOUT settles on the *falling* edge of PD_SCK.
"""

import pytest
from daemon.hx711 import GAIN_PULSES, HX711, W_GPCLR0, W_GPLEV0, W_GPSET0

DT_PIN, SCK_PIN = 5, 6
DT_BIT, SCK_BIT = 1 << DT_PIN, 1 << SCK_PIN


class FakeRegisters:
    """Register file wired to a simulated HX711.

    Before any clocking DOUT sits low (conversion ready). Each falling edge of
    PD_SCK shifts out the next bit, MSB first. Once all 24 data bits plus the
    gain-select pulses are out, DOUT returns high.
    """

    def __init__(self, value: int, gain_pulses: int = 1, *, stuck_low: bool = False):
        self.raw = value & 0xFFFFFF
        self.gain_pulses = gain_pulses
        self.stuck_low = stuck_low
        self.pulses = 0
        self.max_high_phase_reads = 0
        self._sck_high = False
        self._words = [0] * 32

    # -- register access --

    def __getitem__(self, word):
        if word == W_GPLEV0:
            level = 0
            if self._dout():
                level |= DT_BIT
            if self._sck_high:
                level |= SCK_BIT
                # Sampling here would read the previous bit, not this one
                self.max_high_phase_reads += 1
            return level
        return self._words[word]

    def __setitem__(self, word, value):
        if word == W_GPSET0 and value & SCK_BIT:
            self._sck_high = True
        elif word == W_GPCLR0 and value & SCK_BIT:
            if self._sck_high:
                self.pulses += 1
            self._sck_high = False
        else:
            self._words[word] = value

    # -- simulated tag behaviour --

    def _dout(self) -> bool:
        """True means DOUT is high."""
        if self.stuck_low:
            return False
        if self.pulses == 0:
            return False  # conversion ready
        if self.pulses > 24:
            return True  # fully clocked out, back to idle
        bit_index = 24 - self.pulses  # pulse 1 shifts out bit 23
        return bool(self.raw >> bit_index & 1)


def make_scale(value: int, gain: int = 128, **kwargs) -> tuple[HX711, FakeRegisters]:
    """Build an HX711 bound to fake registers, skipping the /dev/gpiomem open."""
    scale = object.__new__(HX711)
    scale._dt_pin = DT_PIN
    scale._sck_pin = SCK_PIN
    scale._gain = gain
    scale._gain_pulses = GAIN_PULSES[gain]
    scale._dt_bit = DT_BIT
    scale._sck_bit = SCK_BIT
    scale._discarded = 0
    scale._reads = 0
    scale._last_accepted = None
    scale._last_status_log = 0.0
    scale._rt_applied = True
    regs = FakeRegisters(value, GAIN_PULSES[gain], **kwargs)
    scale._reg = regs
    return scale, regs


class TestReadOnce:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (0x000000, 0),
            (0x000001, 1),
            (0x7FFFFE, 0x7FFFFE),
            (0xFFFFFF, -1),
            (0x800001, -0x7FFFFF),
            (0x0A5C67, 679015),  # magnitude of a real resting reading
        ],
    )
    def test_decodes_value(self, raw, expected):
        scale, _ = make_scale(raw)
        assert scale._read_once() == expected

    def test_reads_full_24_bits_not_half(self):
        """Regression: sampling before the pulse halves the reading."""
        scale, _ = make_scale(0x0A5C67)
        assert scale._read_once() == 0x0A5C67

    def test_negative_values_survive(self):
        """Regression: a lost MSB turns weight below tare into a huge positive."""
        scale, _ = make_scale(0xFFF000)
        value = scale._read_once()
        assert value < 0

    def test_never_samples_during_high_phase(self):
        """PD_SCK must not be held high across a DOUT read."""
        scale, regs = make_scale(0x123456)
        scale._read_once()
        assert regs.max_high_phase_reads == 0

    def test_clocks_gain_pulses(self):
        scale, regs = make_scale(0x123456, gain=128)
        scale._read_once()
        assert regs.pulses == 24 + GAIN_PULSES[128]

    @pytest.mark.parametrize("gain,pulses", [(128, 1), (32, 2), (64, 3)])
    def test_gain_pulse_counts(self, gain, pulses):
        scale, regs = make_scale(0x000000, gain=gain)
        scale._read_once()
        assert regs.pulses == 24 + pulses

    def test_discards_when_dout_stays_low(self):
        """A power-down mid-read leaves DOUT low — that read is not usable."""
        scale, _ = make_scale(0x123456, stuck_low=True)
        assert scale._read_once() is None

    @pytest.mark.parametrize("raw", [0x7FFFFF, 0x800000])
    def test_discards_saturated(self, raw):
        scale, _ = make_scale(raw)
        assert scale._read_once() is None


class TestReadRaw:
    def test_returns_median_of_samples(self, monkeypatch):
        monkeypatch.setattr("daemon.hx711.SAMPLES", 3)
        scale, _ = make_scale(0)
        values = iter([100, 900, 200])
        scale.wait_data_ready = lambda timeout_s=0.5: True
        scale._read_once = lambda: next(values)
        assert scale.read_raw() == 200

    def test_retries_past_corrupted_samples(self, monkeypatch):
        monkeypatch.setattr("daemon.hx711.SAMPLES", 1)
        scale, _ = make_scale(0)
        values = iter([None, None, 4242])
        scale.wait_data_ready = lambda timeout_s=0.5: True
        scale._read_once = lambda: next(values)
        assert scale.read_raw() == 4242
        assert scale._discarded == 2

    def test_raises_when_nothing_valid(self, monkeypatch):
        monkeypatch.setattr("daemon.hx711.SAMPLES", 1)
        scale, _ = make_scale(0)
        scale.wait_data_ready = lambda timeout_s=0.5: True
        scale._read_once = lambda: None
        with pytest.raises(TimeoutError):
            scale.read_raw()

    def test_raises_when_never_ready(self, monkeypatch):
        monkeypatch.setattr("daemon.hx711.SAMPLES", 1)
        scale, _ = make_scale(0)
        scale.wait_data_ready = lambda timeout_s=0.5: False
        with pytest.raises(TimeoutError):
            scale.read_raw()


class TestJumpConfirmation:
    """A corrupted read can still look well-formed, so jumps get a second look."""

    def _scale(self, monkeypatch, readings, *, jump=100_000):
        monkeypatch.setattr("daemon.hx711.SAMPLES", 1)
        monkeypatch.setattr("daemon.hx711.JUMP_THRESHOLD", jump)
        scale, _ = make_scale(0)
        values = iter(readings)
        scale.wait_data_ready = lambda timeout_s=0.5: True
        scale._read_once = lambda: next(values)
        return scale

    def test_steady_readings_cost_one_conversion(self, monkeypatch):
        scale = self._scale(monkeypatch, [500_000, 500_050])
        assert scale.read_raw() == 500_000
        assert scale.read_raw() == 500_050  # second reading not double-checked

    def test_first_reading_is_taken_as_is(self, monkeypatch):
        scale = self._scale(monkeypatch, [9_000_000])
        assert scale.read_raw() == 9_000_000

    def test_real_load_change_is_confirmed_and_kept(self, monkeypatch):
        scale = self._scale(monkeypatch, [500_000, 900_000, 900_100])
        scale.read_raw()
        assert scale.read_raw() == 900_100

    def test_lone_wild_sample_is_outvoted(self, monkeypatch):
        """The value seen in integration testing: one garbage read among good ones."""
        scale = self._scale(monkeypatch, [678_849, -4_024_596, 678_800, 678_777])
        scale.read_raw()
        # Median of (garbage, 678_800, 678_777) discards the outlier
        assert scale.read_raw() == 678_777

    def test_outvoted_sample_is_counted_as_discarded(self, monkeypatch):
        scale = self._scale(monkeypatch, [678_849, -4_024_596, 678_800, 678_777])
        scale.read_raw()
        before = scale._discarded
        scale.read_raw()
        assert scale._discarded == before + 1

    def test_reference_follows_a_sustained_change(self, monkeypatch):
        scale = self._scale(monkeypatch, [100_000, 900_000, 900_010, 900_020])
        scale.read_raw()
        scale.read_raw()
        assert scale.read_raw() == 900_020  # no further confirmation needed


class TestConstruction:
    def test_rejects_unsupported_gain(self):
        with pytest.raises(ValueError, match="gain"):
            HX711(gain=1)

    def test_rejects_pins_outside_gpfsel0(self):
        with pytest.raises(ValueError, match="GPIO0-9"):
            HX711(dt_pin=17, sck_pin=27)
