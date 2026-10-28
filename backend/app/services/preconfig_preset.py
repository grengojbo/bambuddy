"""Fork: a pre-configured slot gets the spool's own preset, not the slot's old one.

A spool assigned to an empty slot (SpoolBuddy, or a BMCU slot reading blank)
is configured later, by the ``on_ams_change`` replay, when the slot loads. That
replay runs with no user. A cloud user preset (``PFUS...``) is resolved to its
``filament_id`` with the user's Bambu Cloud token, and with auth enabled the
token lives on the user, not in the global settings -- so the replay could not
resolve it, and ``apply_spool_to_slot_via_mqtt`` fell back to the slot's
current ``tray_info_idx``. On a BMCU that is whatever the previous spool left
behind: a white PLA Basic spool went to the printer as Pochatok PLA, because
both are PLA.

So the preset is resolved at assign time, while the user is there, and kept
here keyed by the preset reference; the replay reads it back when its own
resolution comes up empty. The replay also stops passing the slot's old
``tray_info_idx`` (main.py), so a miss here -- after a restart, say -- lands on
the generic preset for the material instead of on another spool's.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.user import User
from backend.app.services.slicer_filament_resolver import resolve_slicer_filament
from backend.app.services.slot_nozzle import resolve_slot_nozzle
from backend.app.services.spool_filament_preset import resolve_spool_preset

logger = logging.getLogger(__name__)

Resolved = tuple[str, str, str | None, str | None]

# slicer_filament -> resolve_slicer_filament() result that carried a filament_id.
_resolved: dict[str, Resolved] = {}


def _key(slicer_filament: str | None) -> str:
    return (slicer_filament or "").strip()


def recall_resolved(slicer_filament: str | None, current_user: User | None, result: Resolved) -> Resolved:
    """Keep a ``resolve_slicer_filament`` result that found a filament_id, or,
    for a lookup with no user that found none, return the one kept."""
    key = _key(slicer_filament)
    # A numeric key is a LocalPreset row id: resolved from the database with no
    # user, and reused by another preset once its row is deleted.
    if not key or key.isdigit():
        return result
    if result[0]:
        _resolved[key] = result
        return result
    if current_user is not None:
        return result
    remembered = _resolved.get(key)
    if remembered:
        logger.info("Slicer preset %s resolved from the assign-time lookup: %s", key, remembered[0])
        return remembered
    return result


async def remember_slot_preset(
    *,
    db: AsyncSession,
    current_user: User | None,
    spool: Spool,
    printer_id: int,
    ams_id: int,
    tray_id: int,
) -> None:
    """Resolve the preset a pending slot will be configured with, while the user is here.

    Picks the preset the way ``apply_spool_to_slot_via_mqtt`` does -- the
    spool's per-model override for this slot's nozzle, else its own -- so the
    replay looks up the same reference.
    """
    from backend.app.services.printer_manager import printer_manager

    model = printer_manager.get_model(printer_id)
    nozzle = resolve_slot_nozzle(printer_manager.get_status(printer_id), ams_id, tray_id, model)
    slicer_filament, slicer_filament_name = await resolve_spool_preset(
        db,
        spool_id=spool.id,
        printer_model=model,
        nozzle_diameter=nozzle.diameter,
        fallback_filament=spool.slicer_filament,
        fallback_name=spool.slicer_filament_name,
    )
    result = await resolve_slicer_filament(
        db=db,
        current_user=current_user,
        slicer_filament=slicer_filament,
        slicer_filament_name=slicer_filament_name,
        material=spool.material,
    )
    recall_resolved(slicer_filament, current_user, result)
