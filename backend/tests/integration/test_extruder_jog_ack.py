"""Extruder jog passes on the printer's refusal (a cold nozzle) instead of claiming success."""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import BambuMQTTClient

ACTIVE = frozenset({"PREPARE", "SLICING", "RUNNING", "PAUSE"})


def _client(answer: dict | None, nozzle: float | None = 220.0) -> BambuMQTTClient:
    client = BambuMQTTClient(ip_address="10.0.0.1", serial_number="SN", access_code="c", model="P1S")
    client._client = MagicMock()
    client.state.connected = True
    client.state.state = "IDLE"
    if nozzle is not None:
        client.state.temperatures["nozzle"] = nozzle

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
        mock_pm.ACTIVE_PRINT_STATES = ACTIVE
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
        mock_pm.ACTIVE_PRINT_STATES = ACTIVE
        mock_pm.get_client.return_value = client
        response = await async_client.post(f"/api/v1/printers/{printer.id}/extruder-jog?distance=-5")
    assert response.status_code == 200
    assert response.json()["confirmed"] is True
    sent = json.loads(client._client.publish.call_args[0][1])["print"]
    assert sent["param"] == "M83\nG1 E-5.00 F300\nM82"


async def _jog(async_client: AsyncClient, printer_factory, client: BambuMQTTClient, distance: float = 10):
    printer = await printer_factory(name="P", model="P1S")
    with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
        mock_pm.ACTIVE_PRINT_STATES = ACTIVE
        mock_pm.get_client.return_value = client
        return await async_client.post(f"/api/v1/printers/{printer.id}/extruder-jog?distance={distance}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refused_while_printing_without_sending(async_client: AsyncClient, printer_factory):
    client = _client({"result": "success"})
    client.state.state = "RUNNING"

    response = await _jog(async_client, printer_factory, client)

    assert response.status_code == 409
    assert "printing" in response.json()["detail"]
    client._client.publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refused_on_a_cold_nozzle_without_sending(async_client: AsyncClient, printer_factory):
    client = _client({"result": "success"}, nozzle=25.0)

    response = await _jog(async_client, printer_factory, client, distance=-5)

    assert response.status_code == 409
    assert "25°C" in response.json()["detail"] and "170°C" in response.json()["detail"]
    client._client.publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refused_while_the_nozzle_temperature_is_unknown(async_client: AsyncClient, printer_factory):
    client = _client({"result": "success"}, nozzle=None)

    response = await _jog(async_client, printer_factory, client)

    assert response.status_code == 409
    client._client.publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_at_the_threshold_it_moves(async_client: AsyncClient, printer_factory):
    client = _client({"result": "success"}, nozzle=170.0)

    response = await _jog(async_client, printer_factory, client)

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("active_extruder", "expected"),
    [(0, 409), (1, 200)],
)
async def test_dual_nozzle_checks_the_active_nozzle(
    async_client: AsyncClient, printer_factory, active_extruder, expected
):
    """nozzle_2 is the right nozzle (extruder 0), nozzle the left (extruder 1)."""
    client = _client({"result": "success"}, nozzle=230.0)
    client.state.temperatures["nozzle_2"] = 30.0
    client.state.active_extruder = active_extruder

    response = await _jog(async_client, printer_factory, client)

    assert response.status_code == expected
