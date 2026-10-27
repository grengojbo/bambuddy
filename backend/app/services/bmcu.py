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
