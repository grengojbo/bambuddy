"""Fork: blank tray reports from a BMCU must not unlink assignments.

An A1 Mini with a BMCU lost its spool assignments in production: after a
power-on, and after some prints, the BMCU reported a slot with blank colour
and type and no presence bit for longer than the unlink grace period, and the
pass deleted the row -- ``fingerprint mismatch (cur=/ fp=FFFFFFFF/PLA ...)``.
The spool never moved.

The printer's ``get_version`` lists that BMCU as ``ams_f1/0`` with
``sw_ver`` ``00.00.00.00``, which is what marks it (services/bmcu.py). A blank
report from such a unit says nothing about the slot, so the assignment stays
until the user unassigns it or assigns another spool there.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Aliased so pytest does not collect the imported test class a second time.
from backend.tests.integration.test_inventory_assign import (
    TestAutoUnlinkOccupiedSlot as _OccupiedSlot,
    _make_printing_status,
    spool_factory,  # noqa: F401 - pytest fixture
)

BMCU = {"module_type": "ams_f1", "sw_ver": "00.00.00.00", "sn": "BMCU0000000001"}
BAMBU_AMS_LITE = {"module_type": "ams_f1", "sw_ver": "00.00.07.48", "sn": "03C00A3B1200001"}


def _blank_unit(**unit_fields):
    return [
        {
            "id": 1,
            **unit_fields,
            "tray": [{"id": 1, "exists": False, "tray_type": "", "tray_color": "", "state": 9}],
        }
    ]


def _status(ams_data):
    return _make_printing_status(ams_data, state="IDLE")


async def _run_past_grace(printer_id, ams_data):
    from unittest.mock import patch

    from backend.app.services import slot_unlink_grace

    clock = [1000.0]
    with patch.object(slot_unlink_grace, "_now", lambda: clock[0]):
        await _OccupiedSlot._run(printer_id, ams_data, _status(ams_data))
        clock[0] += slot_unlink_grace.GRACE_SECONDS
        await _OccupiedSlot._run(printer_id, ams_data, _status(ams_data))


async def _survives(db_session, assignment_id):
    from backend.app.models.spool_assignment import SpoolAssignment

    db_session.expunge_all()
    return await db_session.get(SpoolAssignment, assignment_id) is not None


class TestBmcuBlankTray:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_blank_slot_on_a_bmcu_keeps_the_assignment(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        printer = await printer_factory(name="A1 Mini")
        spool = await spool_factory(material="PLA", rgba="8A8F92FF")
        assignment_id = await _OccupiedSlot._assignment(db_session, printer, spool)

        await _run_past_grace(printer.id, _blank_unit(**BMCU))

        assert await _survives(db_session, assignment_id), (
            "a BMCU blanking a slot past the grace period is not a spool that was removed"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_blank_slot_on_a_bambu_ams_still_unlinks(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        """A real firmware version means a Bambu unit, whose blank slot is believed."""
        printer = await printer_factory(name="A1 Mini")
        spool = await spool_factory(material="PLA", rgba="8A8F92FF")
        assignment_id = await _OccupiedSlot._assignment(db_session, printer, spool)

        await _run_past_grace(printer.id, _blank_unit(**BAMBU_AMS_LITE))

        assert not await _survives(db_session, assignment_id)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_before_the_version_report_a_blank_slot_still_unlinks(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        """No version yet means no evidence of a BMCU, so nothing changes."""
        printer = await printer_factory(name="A1 Mini")
        spool = await spool_factory(material="PLA", rgba="8A8F92FF")
        assignment_id = await _OccupiedSlot._assignment(db_session, printer, spool)

        await _run_past_grace(printer.id, _blank_unit())

        assert not await _survives(db_session, assignment_id)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_different_filament_on_a_bmcu_still_unlinks(
        self, async_client: AsyncClient, printer_factory, spool_factory, db_session: AsyncSession
    ):
        """Only a blank report is shielded; a slot naming another filament is a swap."""
        printer = await printer_factory(name="A1 Mini")
        spool = await spool_factory(material="PLA", rgba="8A8F92FF")
        assignment_id = await _OccupiedSlot._assignment(db_session, printer, spool)

        ams_data = [
            {"id": 1, **BMCU, "tray": [{"id": 1, "exists": True, "tray_type": "PETG", "tray_color": "00FF00FF"}]}
        ]
        await _OccupiedSlot._run(printer.id, ams_data, _status(ams_data))

        assert not await _survives(db_session, assignment_id)


class TestAmsLiteVersionReport:
    def test_an_ams_lite_module_puts_its_version_on_the_unit(self):
        """The A1 Mini's get_version names the AMS Lite ``ams_f1/0``; this is the
        report that carries a BMCU's 00.00.00.00 onto the unit."""
        from backend.app.services.bambu_mqtt import BambuMQTTClient

        client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="0309TEST", access_code="12345678")
        client.state.raw_data["ams"] = [{"id": "0", "tray": []}]

        client._handle_version_info(
            {
                "command": "get_version",
                "module": [
                    {"name": "ota", "sw_ver": "01.07.02.00"},
                    {"name": "ams_f1/0", "product_name": "AMS Lite", "sw_ver": "00.00.00.00", "sn": "BMCU1"},
                ],
            }
        )

        unit = client.state.raw_data["ams"][0]
        assert unit["module_type"] == "ams_f1"
        assert unit["sw_ver"] == "00.00.00.00"
