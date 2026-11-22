"""Control routes report a printer refusal instead of claiming success."""

import functools
import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client(answers: dict[str, dict]) -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model="P1S")
    client._client = MagicMock()
    client.state.connected = True

    def publish(_topic, body, **_kw):
        command = json.loads(body)["print"]
        answer = answers.get(command["command"])
        if answer is not None:
            client._process_message({"print": {**command, **answer}})

    client._client.publish.side_effect = publish
    return client


REFUSE = {"result": "fail", "reason": "device busy"}
ACCEPT = {"result": "success", "reason": "success"}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("url", "command"),
    [
        ("temperature/bed?target=60", "gcode_line"),
        ("temperature/nozzle?target=220", "gcode_line"),
        ("ams-backup?enabled=true", "print_option"),
    ],
)
async def test_refusal_is_a_502_with_the_reason(async_client: AsyncClient, printer_factory, url, command):
    printer = await printer_factory(name="P", model="P1S")
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = _client({command: REFUSE})
        response = await async_client.post(f"/api/v1/printers/{printer.id}/{url}")
    assert response.status_code == 502
    assert "device busy" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_accepted_is_confirmed(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = _client({"gcode_line": ACCEPT})
        response = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/bed?target=60")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["confirmed"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_answer_is_success_unconfirmed(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    client = _client({})
    client.call_with_ack = functools.partial(client.call_with_ack, timeout=0.1)
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = client
        response = await async_client.post(f"/api/v1/printers/{printer.id}/ams-backup?enabled=false")
    assert response.status_code == 200
    assert response.json() == {"success": True, "ams_filament_backup": False, "confirmed": False}
