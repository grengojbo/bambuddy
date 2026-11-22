"""call_with_ack reports the printer's verdict on a command, not just the publish."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient
from backend.app.services.mqtt_ack import AckStatus


def _client(answers: dict[str, dict | None]) -> tuple[BambuMQTTClient, list[dict]]:
    """A connected client whose printer answers each command per ``answers``.

    The answer is fed back synchronously from inside publish, i.e. before the
    caller gets back to awaiting — the race arming before publish exists for.
    """
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model="P1S")
    client._client = MagicMock()
    client.state.connected = True
    sent: list[dict] = []

    def publish(_topic, body, **_kw):
        command = json.loads(body)["print"]
        sent.append(command)
        answer = answers.get(command["command"])
        if answer is not None:
            client._process_message({"print": {**command, **answer}})

    client._client.publish.side_effect = publish
    return client, sent


class TestCallWithAck:
    @pytest.mark.asyncio
    async def test_temperature_accepted(self):
        client, sent = _client({"gcode_line": {"result": "success", "reason": "success"}})

        ack = await client.call_with_ack(client.set_bed_temperature, 60, timeout=1)

        assert ack.status == AckStatus.ACCEPTED and ack.confirmed
        assert ack.sequence_id == sent[0]["sequence_id"]
        assert sent[0]["param"] == "M140 S60"
        assert client._acks.pending_count == 0

    @pytest.mark.asyncio
    async def test_temperature_refused_with_reason(self):
        client, _ = _client({"gcode_line": {"result": "fail", "reason": "device busy"}})

        ack = await client.call_with_ack(client.set_nozzle_temperature, 220, 0, timeout=1)

        assert ack.refused
        assert ack.reason == "device busy"

    @pytest.mark.asyncio
    async def test_print_option_refused(self):
        client, sent = _client({"print_option": {"result": "fail", "reason": "not supported"}})

        ack = await client.call_with_ack(client.set_ams_filament_backup, True, timeout=1)

        assert ack.refused and ack.reason == "not supported"
        assert sent[0]["auto_switch_filament"] is True

    @pytest.mark.asyncio
    async def test_no_answer_is_a_timeout_not_a_refusal(self):
        client, _ = _client({})

        ack = await client.call_with_ack(client.set_bed_temperature, 60, timeout=0.1)

        assert ack.status == AckStatus.TIMEOUT
        assert not ack.refused and not ack.confirmed
        assert client._acks.pending_count == 0

    @pytest.mark.asyncio
    async def test_not_connected_is_not_sent(self):
        client, sent = _client({})
        client.state.connected = False

        ack = await client.call_with_ack(client.set_bed_temperature, 60)

        assert ack.status == AckStatus.NOT_SENT
        assert sent == []

    @pytest.mark.asyncio
    async def test_disconnect_ends_the_wait(self):
        client, _ = _client({})
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, client._on_disconnect, None, None)

        started = loop.time()
        ack = await client.call_with_ack(client.set_bed_temperature, 60, timeout=5)

        assert ack.status == AckStatus.DISCONNECTED
        assert loop.time() - started < 1

    @pytest.mark.asyncio
    async def test_concurrent_commands_get_their_own_answers(self):
        client, sent = _client({})

        def answer():
            # The printer answers the second command first.
            bed, nozzle = sent
            client._process_message({"print": {**nozzle, "result": "fail", "reason": "nozzle busy"}})
            client._process_message({"print": {**bed, "result": "success"}})

        asyncio.get_running_loop().call_later(0.05, answer)
        bed_ack, nozzle_ack = await asyncio.gather(
            client.call_with_ack(client.set_bed_temperature, 60, timeout=2),
            client.call_with_ack(client.set_nozzle_temperature, 220, timeout=2),
        )

        assert bed_ack.status == AckStatus.ACCEPTED and bed_ack.sequence_id == sent[0]["sequence_id"]
        assert nozzle_ack.refused and nozzle_ack.reason == "nozzle busy"
        assert bed_ack.sequence_id != nozzle_ack.sequence_id

    @pytest.mark.asyncio
    async def test_answer_for_another_command_with_our_sequence_is_ignored(self):
        client, sent = _client({})

        def foreign_answer():
            # A slicer's command that happens to reuse our sequence number.
            client._process_message(
                {"print": {"command": "print_option", "sequence_id": sent[0]["sequence_id"], "result": "fail"}}
            )

        asyncio.get_running_loop().call_later(0.02, foreign_answer)
        ack = await client.call_with_ack(client.set_bed_temperature, 60, timeout=0.2)

        assert ack.status == AckStatus.TIMEOUT

    def test_plain_send_leaves_nothing_armed(self):
        """Callers that do not wait (the print scheduler) leave no entries behind."""
        client, _ = _client({})

        assert client.set_bed_temperature(0) is True
        assert client.set_ams_filament_backup(False) is True
        assert client._acks.pending_count == 0
