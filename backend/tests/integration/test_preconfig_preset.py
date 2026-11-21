"""Fork: the pre-config replay never configures a slot as the spool that left it.

Production, A1 Mini with a BMCU: spool 15 (white PLA Basic, preset
PFUS51e32689bf11e5) was pre-assigned to an empty slot that had last held a
Pochatok PLA spool. When the slot loaded, the replay could not resolve the
PFUS and fell back to the slot's leftover tray_info_idx -- the slicer showed
the slot as Pochatok.

Resolving the preset with the assigner's cloud sign-in is upstream's fix
(#3519, assigned_by_user_id). What stays in the fork is main.py not offering
the slot's leftover id as a fallback, so a preset that still can't be looked
up lands on the material's generic id.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.tests.integration.test_inventory_assign import (
    _make_mock_status,
    spool_factory,  # noqa: F401 - pytest fixture
)

SPOOL_PRESET = "PFUS51e32689bf11e5"
SPOOL_FILAMENT_ID = "P275db60"
PREVIOUS_SPOOLS_ID = "P7a0f873"


async def _cloud_needs_a_user(*, db, current_user, slicer_filament, slicer_filament_name, material):
    """The resolver as it behaves with the cloud token on the user only."""
    if current_user is None:
        return ("", slicer_filament, None, None)
    return (SPOOL_FILAMENT_ID, slicer_filament, "BambuLab PLA Basic", "PLA")


async def _replay(printer, spool, db_session):
    """Pre-assignment to an empty slot, then the slot loads with the previous spool's id."""
    from backend.app.main import on_ams_change
    from backend.app.models.spool_assignment import SpoolAssignment

    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=2))
    await db_session.commit()

    ams_data = [
        {
            "id": 0,
            "tray": [
                {"id": 2, "tray_type": "PLA", "tray_color": "FFFFFFFF", "state": 3, "tray_info_idx": PREVIOUS_SPOOLS_ID}
            ],
        }
    ]
    client = MagicMock()
    status = _make_mock_status(ams_data=ams_data)

    with (
        patch("backend.app.main.printer_manager") as pm_main,
        patch("backend.app.services.printer_manager.printer_manager") as pm,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.api.routes.inventory.resolve_slicer_filament", _cloud_needs_a_user),
    ):
        pm_main.get_printer.return_value = MagicMock(name="A1 mini", serial_number="0309CA391800999")
        pm_main.get_status.return_value = status
        pm_main.get_client.return_value = client
        pm_main.get_model.return_value = "A1 mini"
        pm.get_client.return_value = client
        pm.get_status.return_value = status
        pm.get_model.return_value = "A1 mini"
        relay.on_ams_change = AsyncMock()
        ws.send_printer_status = AsyncMock()
        ws.broadcast = AsyncMock()

        await on_ams_change(printer.id, ams_data)

    client.ams_set_filament_setting.assert_called_once()
    return client.ams_set_filament_setting.call_args.kwargs["tray_info_idx"]


class TestPreconfigReplayPreset:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_replay_does_not_take_the_previous_spools_preset(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        printer = await printer_factory(name="A1 mini")
        spool = await spool_factory(slicer_filament=SPOOL_PRESET, material="PLA", rgba="FFFFFFFF")

        tray_info_idx = await _replay(printer, spool, db_session)

        assert tray_info_idx != PREVIOUS_SPOOLS_ID, "the slot was configured as the spool that left it"
        assert tray_info_idx == "GFL99"
