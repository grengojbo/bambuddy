"""Fork: is this AMS unit a BMCU?

A BMCU (open-source AMS replacement, github.com/jarczakpawel/BMCU-C-PJARCZAK)
is listed in the printer's ``get_version`` report like a Bambu AMS -- on an
A1 Mini in AMS Lite mode as ``ams_f1/0``, product "AMS Lite", with a serial --
but its ``sw_ver`` is ``00.00.00.00``. A Bambu AMS always reports its real
firmware version, so the all-zero version is what tells a BMCU apart. It is
also why the printer raises HMS 0500-0400-0001-0044 ("The firmware of AMS A
does not match the printer") against it.

The version lands on the unit as ``sw_ver`` via
``BambuMQTTClient._apply_ams_version_cache``. Before the version report
arrives no unit has one, and nothing is treated as a BMCU.

Kept out of ``main.py`` so the upstream file carries only the call site.
"""

from collections.abc import Iterable
from typing import Any

BMCU_SW_VER = "00.00.00.00"

# "The firmware of AMS A does not match the printer" -- raised by A1 firmware
# 01.07.xx against a BMCU and harmless there (BMCU-C-PJARCZAK README).
BMCU_FIRMWARE_MISMATCH = "0500040000010044"

_AMS_MODULE_TYPES = frozenset({"ams", "ams_f1", "n3f", "n3s"})


def ams_unit_is_bmcu(ams_data: Iterable[Any] | None, ams_id: int) -> bool:
    """True when the AMS unit ``ams_id`` reports the BMCU's all-zero firmware."""
    for unit in ams_data or ():
        if not isinstance(unit, dict):
            continue
        try:
            unit_id = int(unit.get("id"))
        except (TypeError, ValueError):
            continue
        if unit_id == ams_id:
            return unit.get("sw_ver") == BMCU_SW_VER
    return False


def bmcu_tray_blank(tray_color: str, tray_type: str) -> bool:
    """A tray report that says nothing about the filament in it.

    No type, and a colour that is empty or all zeros: right after the printer
    is switched on a BMCU reports ``tray_color`` ``00000000``, not ``""``.
    """
    return not (tray_type or "").strip() and not (tray_color or "").strip().strip("0")


def ams_versions_from_modules(modules: Iterable[Any] | None) -> list[str]:
    """The ``sw_ver`` of every AMS module in a ``get_version`` module list."""
    if not isinstance(modules, list):
        return []
    return [
        str(m.get("sw_ver") or "")
        for m in modules or ()
        if isinstance(m, dict) and str(m.get("name", "")).split("/", 1)[0] in _AMS_MODULE_TYPES
    ]


def hide_bmcu_hms(full_code: str, ams_versions: Iterable[str] | None) -> bool:
    """Should this HMS fault be hidden as the BMCU firmware mismatch?

    Only the mismatch code, and only where an AMS reports the BMCU version.
    With no AMS versions known yet it is hidden too: after a restart the first
    push can arrive before ``get_version``, and a notification sent then could
    not be taken back. A Bambu AMS shows it again on the next push that
    carries the fault once its real version is known.
    """
    if full_code.upper() != BMCU_FIRMWARE_MISMATCH:
        return False
    versions = list(ams_versions or ())
    return not versions or BMCU_SW_VER in versions


def _hex_int(value: Any) -> int:
    if isinstance(value, str):
        return int(value.replace("0x", ""), 16) if value else 0
    return int(value or 0)


def drop_bmcu_hms(hms_list: Any, ams_versions: Iterable[str] | None) -> Any:
    """``hms_list`` (raw ``[{attr, code}]``) without the BMCU firmware mismatch."""
    if not isinstance(hms_list, list):
        return hms_list
    versions = list(ams_versions or ())
    kept = []
    for hms in hms_list:
        if isinstance(hms, dict):
            try:
                full_code = f"{_hex_int(hms.get('attr')):08X}{_hex_int(hms.get('code')):08X}"
            except (TypeError, ValueError):
                full_code = ""
            if hide_bmcu_hms(full_code, versions):
                continue
        kept.append(hms)
    return kept
