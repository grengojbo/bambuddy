#!/usr/bin/env python3
"""MFRC522 NFC Diagnostic.

SPI0 with the kernel's own chip select (CE0, pin 24). RST on GPIO25 (pin 22)
is optional — a soft reset works without it.

Note: `dtoverlay=spi0-0cs` must NOT be present in config.txt. It removes CE0,
which the PN5180 wants but this reader needs.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from daemon.bambu_keys import TRAY_UUID_BLOCK  # noqa: E402
from daemon.mfrc522 import MFRC522, _version_name  # noqa: E402

SCAN_SECONDS = 20


def printable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def describe_tag(sak: int) -> str:
    if sak in (0x08, 0x18):
        return "MIFARE Classic (Bambu filament tag)"
    if sak in (0x00, 0x04):
        return "NTAG / NDEF (SpoolEase, OpenPrintTag)"
    return "unrecognised"


def main():
    try:
        reader = MFRC522()
    except Exception as e:
        print(f"FAIL: {e}")
        print("  - is the module powered from 3.3V (pin 1)? 5V destroys it")
        print("  - is SDA wired to GPIO8 (pin 24, CE0)?")
        print("  - check that config.txt has no 'dtoverlay=spi0-0cs'")
        sys.exit(1)

    try:
        version = reader.read_reg(0x37)
        print(f"Version:  0x{version:02X}  ({_version_name(version)})")

        result = reader.self_test()
        unique = len(set(result))
        print(f"Selftest: {unique} distinct bytes of 64  {'OK' if unique > 20 else 'SUSPECT'}")
        print("          " + " ".join(f"{b:02X}" for b in result[:16]) + " ...")

        print(f"\nPresent a tag ({SCAN_SECONDS}s)...")
        deadline = time.monotonic() + SCAN_SECONDS
        activation = None
        while time.monotonic() < deadline and activation is None:
            activation = reader.activate_type_a()
            time.sleep(0.1)

        if activation is None:
            print("  No tag seen. If the reader is otherwise healthy, hold the")
            print("  tag flat against the antenna — read range is only a few cm.")
            return

        uid, sak = activation
        print(f"  UID:  {uid.hex().upper()}  ({len(uid)} bytes)")
        print(f"  SAK:  0x{sak:02X}  -> {describe_tag(sak)}")

        if sak in (0x08, 0x18):
            blocks = reader.read_bambu_tag(uid)
            if not blocks:
                print("\n  Could not read Bambu data — HKDF authentication failed.")
                print("  Third-party MIFARE tags use different keys; that is expected.")
                return
            print()
            for number in sorted(blocks):
                data = blocks[number]
                print(f"  block {number:2}: {data.hex().upper()}  |{printable(data)}|")
            tray = blocks.get(TRAY_UUID_BLOCK)
            if tray:
                print(f"\n  tray_uuid: {tray.hex().upper()}")

        elif sak in (0x00, 0x04):
            capability = reader.ntag_read_pages(start_page=3, num_pages=1)
            if capability:
                print(f"  CC:   {capability.hex().upper()}  ({capability[2] * 8} bytes usable)")
            data = reader.read_ntag(uid)
            if data:
                print(f"\n  pages 4-20 ({len(data)} bytes):")
                for offset in range(0, len(data), 4):
                    chunk = data[offset : offset + 4]
                    print(f"  page {4 + offset // 4:2}: {chunk.hex().upper()}  |{printable(chunk)}|")

    finally:
        reader.close()


if __name__ == "__main__":
    main()
