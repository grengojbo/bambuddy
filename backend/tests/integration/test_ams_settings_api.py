"""POST /printers/{id}/ams-settings and the status fields it drives."""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import BambuMQTTClient

URL = "ams-settings?insertion_read=true&startup_read=false&remain_capacity=true"


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
async def test_accepted(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    client = _client({"result": "success"})
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = client
        response = await async_client.post(f"/api/v1/printers/{printer.id}/{URL}")
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "ams_insertion_read": True,
        "ams_startup_read": False,
        "ams_remain_capacity": True,
        "confirmed": True,
    }
    sent = json.loads(client._client.publish.call_args[0][1])["print"]
    assert (sent["tray_read_option"], sent["startup_read_option"], sent["calibrate_remain_flag"]) == (
        True,
        False,
        True,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refusal_is_a_502(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = _client({"result": "fail", "reason": "AMS busy"})
        response = await async_client.post(f"/api/v1/printers/{printer.id}/{URL}")
    assert response.status_code == 502
    assert "AMS busy" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_all_three_flags_are_required(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    response = await async_client.post(f"/api/v1/printers/{printer.id}/ams-settings?insertion_read=true")
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_not_connected(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(name="P", model="P1S")
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.get_client.return_value = None
        response = await async_client.post(f"/api/v1/printers/{printer.id}/{URL}")
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unknown_printer(async_client: AsyncClient):
    response = await async_client.post(f"/api/v1/printers/99999/{URL}")
    assert response.status_code == 404
