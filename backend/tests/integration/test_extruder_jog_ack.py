"""Extruder jog passes on the printer's refusal (a cold nozzle) instead of claiming success."""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client(answer: dict | None) -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model="P1S")
    client._client = MagicMock()
    client.state.connected = True

    def publish(_topic, body, **_kw):
        command = json.loads(body)["print"]
        if answer is not None:
            client._process_message({"print": {**command, **answer}})

    client._client.publish.side_effect = publish
    return client


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cold_nozzle_refusal_is_a_502(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = _client({"result": "fail", "reason": "nozzle temperature too low"})
        response = await async_client.post(f"/api/v1/printers/{printer.id}/extruder-jog?distance=10")
    assert response.status_code == 502
    assert "nozzle temperature too low" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_accepted_jog_is_confirmed(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    client = _client({"result": "success", "reason": "success"})
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = client
        response = await async_client.post(f"/api/v1/printers/{printer.id}/extruder-jog?distance=-5")
    assert response.status_code == 200
    assert response.json()["confirmed"] is True
    sent = json.loads(client._client.publish.call_args[0][1])["print"]
    assert sent["param"] == "M83\nG1 E-5.00 F300\nM82"
