"""Bambu Studio's AMS Settings: RFID read on insertion / power-on, remaining-filament estimate."""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.mqtt_ack import AckStatus


def _client(answer: dict | None = None) -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model="P1S")
    client._client = MagicMock()
    client.state.connected = True

    def publish(_topic, body, **_kw):
        command = json.loads(body)["print"]
        if answer is not None:
            client._process_message({"print": {**command, **answer}})

    client._client.publish.side_effect = publish
    return client


def _push(client: BambuMQTTClient, **flags):
    client._process_message({"print": {"ams": {"ams": [], **flags}}})


def _flags(client: BambuMQTTClient):
    state = client.state
    return state.ams_insertion_read, state.ams_startup_read, state.ams_remain_capacity


class TestReadingTheFlags:
    def test_unknown_until_reported(self):
        assert _flags(_client()) == (None, None, None)

    def test_read_from_print_ams(self):
        client = _client()
        _push(client, insert_flag=True, power_on_flag=False, calibrate_remain_flag=True)
        assert _flags(client) == (True, False, True)

    def test_a_push_without_the_flags_keeps_them(self):
        client = _client()
        _push(client, insert_flag=True, power_on_flag=True, calibrate_remain_flag=False)
        _push(client, tray_now="255")
        assert _flags(client) == (True, True, False)

    def test_non_bool_values_are_ignored(self):
        client = _client()
        _push(client, insert_flag="yes", power_on_flag=1)
        assert _flags(client) == (None, None, None)


class TestSettingThem:
    def test_command_shape(self):
        client = _client()

        assert client.set_ams_user_setting(insertion_read=False, startup_read=True, remain_capacity=True)

        sent = json.loads(client._client.publish.call_args[0][1])["print"]
        assert sent["command"] == "ams_user_setting"
        assert sent["ams_id"] == -1
        assert sent["tray_read_option"] is False
        assert sent["startup_read_option"] is True
        assert sent["calibrate_remain_flag"] is True
        assert sent["sequence_id"] != "0"

    def test_not_connected(self):
        client = _client()
        client.state.connected = False
        assert client.set_ams_user_setting(True, True, True) is False
        client._client.publish.assert_not_called()

    def test_stale_push_does_not_flip_the_toggles_back(self):
        client = _client()
        _push(client, insert_flag=True, power_on_flag=True, calibrate_remain_flag=True)

        client.set_ams_user_setting(insertion_read=False, startup_read=True, remain_capacity=True)
        _push(client, insert_flag=True, power_on_flag=True, calibrate_remain_flag=True)
        assert _flags(client) == (False, True, True)

        # Once the hold has passed, the printer's report wins again.
        client._xcam_hold_start["ams_user_setting"] -= client._xcam_hold_time + 1
        _push(client, insert_flag=True, power_on_flag=True, calibrate_remain_flag=True)
        assert _flags(client) == (True, True, True)

    @pytest.mark.asyncio
    async def test_refusal_comes_back_through_call_with_ack(self):
        client = _client({"result": "fail", "reason": "AMS busy"})

        ack = await client.call_with_ack(client.set_ams_user_setting, True, False, True, timeout=1)

        assert ack.refused and ack.reason == "AMS busy"

    @pytest.mark.asyncio
    async def test_accepted(self):
        client = _client({"result": "success"})

        ack = await client.call_with_ack(client.set_ams_user_setting, True, False, True, timeout=1)

        assert ack.status == AckStatus.ACCEPTED and ack.confirmed
