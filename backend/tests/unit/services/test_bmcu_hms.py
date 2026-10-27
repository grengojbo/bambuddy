"""Fork: HMS 0500-0400-0001-0044 is hidden for a BMCU.

"The firmware of AMS A does not match the printer" is what an A1 Mini on
01.07.xx raises against a BMCU, which reports AMS firmware 00.00.00.00. It does
not affect printing (BMCU-C-PJARCZAK README, printer compatibility table), but
it reached the printer card, Telegram and, through the virtual printer, Bambu
Studio. It is dropped where a BMCU is present, and kept on a Bambu AMS.
"""

from unittest.mock import MagicMock

import pytest

MISMATCH = {"attr": 0x05000400, "code": 0x00010044}
OTHER_FAULT = {"attr": 0x05000000, "code": 0x4038}

BMCU_MODULES = [
    {"name": "ota", "sw_ver": "01.07.02.00"},
    {"name": "ams_f1/0", "product_name": "AMS Lite", "sw_ver": "00.00.00.00", "sn": "BMCU1"},
]
BAMBU_AMS_MODULES = [
    {"name": "ota", "sw_ver": "01.07.02.00"},
    {"name": "ams_f1/0", "product_name": "AMS Lite", "sw_ver": "00.00.07.48", "sn": "03C00A3B1200001"},
]


def _client(modules):
    from backend.app.services.bambu_mqtt import BambuMQTTClient

    client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="0309TEST", access_code="12345678")
    if modules is not None:
        client._handle_version_info({"command": "get_version", "module": modules})
    return client


def _codes(client):
    return [e.full_code for e in client.state.hms_errors]


class TestPrinterStateHms:
    """The printer card and the notification path both read state.hms_errors."""

    def test_the_mismatch_is_dropped_on_a_bmcu(self):
        client = _client(BMCU_MODULES)
        client._update_state({"hms": [MISMATCH, OTHER_FAULT]})
        assert _codes(client) == ["0500000000004038"]

    def test_the_mismatch_stays_on_a_bambu_ams(self):
        client = _client(BAMBU_AMS_MODULES)
        client._update_state({"hms": [MISMATCH]})
        assert _codes(client) == ["0500040000010044"]

    def test_the_mismatch_is_held_back_until_the_version_report(self):
        """After a restart the first push can beat get_version; notifying then
        would send exactly the message this exists to stop."""
        client = _client(None)
        client._update_state({"hms": [MISMATCH]})
        assert _codes(client) == []


class TestVirtualPrinterHms:
    @staticmethod
    async def _vp_hms(modules):
        from backend.tests.unit.test_vp_mqtt_bridge import _capture_published, _make_server

        server = _make_server()
        bridge = MagicMock()
        bridge.get_latest_print_state.return_value = {
            "command": "push_status",
            "msg": 0,
            "hms": [MISMATCH, OTHER_FAULT],
        }
        bridge.get_latest_version_modules.return_value = modules
        server.set_bridge(bridge)
        published = _capture_published(server)

        await server._send_status_report(MagicMock())
        return published[0][1]["print"]["hms"]

    @pytest.mark.asyncio
    async def test_the_slicer_does_not_see_the_mismatch_on_a_bmcu(self):
        assert await self._vp_hms(BMCU_MODULES) == [OTHER_FAULT]

    @pytest.mark.asyncio
    async def test_the_slicer_still_sees_it_on_a_bambu_ams(self):
        assert await self._vp_hms(BAMBU_AMS_MODULES) == [MISMATCH, OTHER_FAULT]


class TestHelper:
    def test_string_codes_are_matched_too(self):
        """Some firmware sends attr/code as hex strings."""
        from backend.app.services.bmcu import drop_bmcu_hms

        hms = [{"attr": "0x05000400", "code": "0x00010044"}]
        assert drop_bmcu_hms(hms, ["00.00.00.00"]) == []
