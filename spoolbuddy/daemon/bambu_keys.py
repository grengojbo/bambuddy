"""Bambu Lab MIFARE Classic key derivation.

Shared by all NFC frontend drivers (PN5180, MFRC522) — the key schedule is a
property of the tag, not of the reader hardware.

Bambu Lab filament tags are MIFARE Classic 1K/4K. Each sector is protected by
a Key A derived from the tag UID via HKDF-SHA256 with a fixed master key.
"""

import hashlib
import hmac

# Bambu Lab MIFARE Classic key derivation constants (from pico-nfc-bridge.ino)
BAMBU_MASTER_KEY = bytes(
    [
        0x9A,
        0x75,
        0x9C,
        0xF2,
        0xC4,
        0xF7,
        0xCA,
        0xFF,
        0x22,
        0x2C,
        0xB9,
        0x76,
        0x9B,
        0x41,
        0xBC,
        0x96,
    ]
)
BAMBU_CONTEXT = b"RFID-A\x00"  # 7 bytes including null terminator

# Blocks to read for Bambu tag data.
#   1 — material variant ID + filament ID   ("A00-W1", "GFA00")
#   2 — filament type                       ("PLA")
#   4 — detailed filament type              ("PLA Basic")
#   5 — colour RGBA, spool weight, diameter
#   9 — tray UID, the tag's unique identifier
BAMBU_BLOCKS = [1, 2, 4, 5, 9]

# Block holding the 16-byte tray UID that identifies an individual spool
TRAY_UUID_BLOCK = 9


def hkdf_derive_keys(uid: bytes) -> bytes:
    """Derive 96 bytes of MIFARE key material (16 sectors * 6 bytes each).

    Uses HKDF-SHA256 with the Bambu master key as salt and the tag UID as IKM.
    """
    # HKDF-Extract: PRK = HMAC-SHA256(salt=master_key, IKM=uid)
    prk = hmac.new(BAMBU_MASTER_KEY, uid, hashlib.sha256).digest()

    # HKDF-Expand: generate 96 bytes using context "RFID-A\0"
    okm = b""
    t = b""
    counter = 1
    while len(okm) < 96:
        t = hmac.new(prk, t + BAMBU_CONTEXT + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:96]


def get_sector_key(keys: bytes, block: int) -> bytes:
    """Get the 6-byte key for the sector containing the given block."""
    sector = block // 4
    return keys[sector * 6 : sector * 6 + 6]
