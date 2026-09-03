#!/usr/bin/env python3
"""HX711 Scale Diagnostic.

Bit-banged over /dev/gpiomem: DT on GPIO5 (pin 29), SCK on GPIO6 (pin 31).

Reports the resting value, its spread, and how many reads were discarded
because PD_SCK stayed high past the HX711's 60 us power-down threshold. A few
tenths of a percent is normal; several percent means the machine is too loaded
or the process needs CAP_SYS_NICE for real-time scheduling.
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from daemon.hx711 import HX711  # noqa: E402

SAMPLE_COUNT = 20
LOAD_TEST_SECONDS = 10
# A 5 kg cell at 1 mV/V on a 3.3 V rail lands near this many counts per gram
COUNTS_PER_GRAM = 420.0


def main():
    scale = HX711()
    print(f"Driver:   {scale.describe()}")

    try:
        scale.init()
    except TimeoutError as e:
        print(f"\nFAIL: {e}")
        print("  - is the module powered from 3.3V (pin 17), not 5V?")
        print("  - is DT wired to GPIO5 (pin 29) and SCK to GPIO6 (pin 31)?")
        sys.exit(1)

    try:
        print(f"\nReading {SAMPLE_COUNT} samples...")
        started = time.monotonic()
        values = []
        for _ in range(SAMPLE_COUNT):
            try:
                values.append(scale.read_raw())
            except TimeoutError as e:
                print(f"  read failed: {e}")
        elapsed = time.monotonic() - started

        if not values:
            print("\nFAIL: no usable readings")
            sys.exit(1)

        resting = statistics.median(values)
        spread = max(values) - min(values)
        print(f"  median:   {resting:>12,.0f}")
        print(f"  spread:   {spread:>12,}  (~{spread / COUNTS_PER_GRAM:.2f} g)")
        print(f"  rate:     {len(values) / elapsed:>12.1f} Hz")
        print(f"  discarded:{scale._discarded:>12}  of {scale._reads} reads")

        if spread > 50_000:
            print("\n  WARNING: very noisy — check E+/E-/A+/A- wiring and the cable shield")

        print(f"\nLoad the cell now ({LOAD_TEST_SECONDS}s)...")
        deadline = time.monotonic() + LOAD_TEST_SECONDS
        peak = 0
        while time.monotonic() < deadline:
            try:
                delta = scale.read_raw() - resting
            except TimeoutError:
                continue
            if abs(delta) > abs(peak):
                peak = delta

        print(f"  peak deviation: {peak:+,.0f}  (~{peak / COUNTS_PER_GRAM:+.0f} g)")
        if abs(peak) > 20_000:
            print("  OK: the cell responds to load")
        else:
            print("  No response. The beam must be mounted so it can bend:")
            print("  one end fixed, the other free, with spacers in between.")
            print("  An unmounted cell lying flat deforms by almost nothing.")

    finally:
        scale.close()


if __name__ == "__main__":
    main()
