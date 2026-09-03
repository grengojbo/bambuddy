"""Shared helpers for normalizing RFID tag and tray identifiers."""


def normalize_hex(value: str | None) -> str:
    if not value:
        return ""
    hex_chars = "".join(ch for ch in str(value).strip() if ch in "0123456789abcdefABCDEF")
    return hex_chars.upper()


def normalize_tag_uid(value: str | None) -> str:
    uid = normalize_hex(value)
    # DB column is VARCHAR(16), so keep the least-significant bytes if longer.
    if len(uid) > 16:
        uid = uid[-16:]
    return uid


def normalize_tray_uuid(value: str | None) -> str:
    uuid = normalize_hex(value)
    # DB column is VARCHAR(32). Keep canonical 32-char UUID when possible.
    if len(uuid) >= 32:
        uuid = uuid[:32]
    return uuid


def is_text_artifact_tray_uuid(value: str | None) -> bool:
    """True when a tray_uuid is really a mis-decoded ASCII text field.

    SpoolBuddy daemons that read the tray UUID from Bambu tag blocks 4-5 pick up
    the filament description rather than the per-spool UUID in block 9. The
    result is well-formed 32-char hex -- ``504C4120426173696300000000000000``
    is the hex of ``"PLA Basic"`` -- and identical for every spool of that
    product, so neither the length check nor the hex pattern on the request
    schema rejects it.

    What gives it away is the shape: printable ASCII followed by NUL padding. A
    genuine tray UUID is 16 random bytes, so the odds of one landing in that
    shape are around 1e-9 and treating the shape as proof is safe.
    """
    uuid = normalize_tray_uuid(value)
    if len(uuid) != 32:
        return False

    text = bytes.fromhex(uuid).rstrip(b"\x00")
    if not text:  # all zeroes -- invalid, but not a text artifact
        return False
    return all(0x20 <= byte <= 0x7E for byte in text)
