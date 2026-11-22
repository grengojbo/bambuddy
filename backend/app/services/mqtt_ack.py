"""Acknowledgements for commands published to a Bambu printer over MQTT.

The printer answers most ``print`` commands on the report topic with the same
``command`` and ``sequence_id`` it was sent, plus ``result`` and ``reason``.
A successful ``publish`` only means paho queued the packet; the answer is the
printer's verdict. K-profile writes (#2718) and RFID re-reads (#3206) each
grew their own registry for that answer. ``AckTracker`` is that registry made
general, so any command can report what the printer actually said.

Threading: ``resolve`` runs on paho's network thread, ``wait`` on the event
loop. The shared state is one dict behind a lock, and ``wait`` polls it every
50 ms rather than hopping threads with ``call_soon_threadsafe`` — the answer
usually lands in 10-150 ms, so polling costs a handful of lookups and needs no
loop reference on the MQTT side (the same trade-off ``await_cali_ack`` made).

An entry is armed *before* the publish: the printer can answer faster than an
async caller gets back to awaiting, and an answer for an unarmed sequence_id
is dropped.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# Default time the printer has to answer. Measured answers are 11 ms
# (ams_get_rfid, #3206) to 150 ms (extrusion_cali_set, #2718); the margin is
# for a busy printer mid-print. Kept short because firmware that never answers
# a command makes every caller wait this long (the RFID refresh uses 3 s too).
DEFAULT_ACK_TIMEOUT = 3.0

_POLL_INTERVAL = 0.05


class AckStatus(StrEnum):
    ACCEPTED = "accepted"
    # The printer answered with result "fail".
    REFUSED = "refused"
    # No answer in time. Not a refusal: older firmware does not answer every
    # command, so callers treat it as "sent, unconfirmed".
    TIMEOUT = "timeout"
    # The connection dropped while waiting; the command may or may not have
    # reached the printer.
    DISCONNECTED = "disconnected"
    # The command was never published (client not connected, bad input).
    NOT_SENT = "not_sent"


@dataclass(frozen=True)
class AckResult:
    status: AckStatus
    command: str = ""
    sequence_id: str = ""
    reason: str = ""
    payload: dict | None = None

    @property
    def refused(self) -> bool:
        return self.status == AckStatus.REFUSED

    @property
    def confirmed(self) -> bool:
        """True only when the printer itself said the command was accepted."""
        return self.status == AckStatus.ACCEPTED and self.payload is not None


def verdict(payload: dict) -> tuple[AckStatus, str]:
    """Read the printer's verdict out of an answer.

    Only ``result: fail`` (any case — X1Plus sends ``FAIL``) counts as a
    refusal, matching the existing K-profile and RFID handling. A non-zero
    ``err_code`` is appended to the reason so it reaches the user.
    """
    result = str(payload.get("result", "") or "").lower()
    reason = str(payload.get("reason", "") or "")
    err_code = payload.get("err_code")
    if result == "fail":
        if err_code not in (None, 0, "0", ""):
            reason = f"{reason} (err_code {err_code})" if reason else f"err_code {err_code}"
        return AckStatus.REFUSED, reason or "printer reported failure"
    return AckStatus.ACCEPTED, "" if reason.lower() == "success" else reason


# Sequence ids armed by note_publish while a capture() block is active.
_capture: contextvars.ContextVar[list[tuple[str, str]] | None] = contextvars.ContextVar(
    "mqtt_ack_capture", default=None
)


@dataclass
class _Pending:
    command: str
    payload: dict | None = None
    # Set by fail_all; the waiter reports it instead of a payload.
    status: AckStatus | None = None


class AckTracker:
    """Correlates published commands with the printer's answers by sequence_id."""

    def __init__(self, label: str = "") -> None:
        self._label = label
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    # -- registry ---------------------------------------------------------

    def arm(self, sequence_id: str, command: str) -> None:
        """Start listening for the answer to ``command`` sent as ``sequence_id``.

        ``"0"`` is refused: commands published with a hardcoded ``"0"`` (stop,
        pause, ams_control, ...) share it, so their answers cannot be told apart.
        """
        sequence_id = str(sequence_id)
        if not sequence_id or sequence_id == "0":
            raise ValueError(f"sequence_id {sequence_id!r} cannot be correlated")
        with self._lock:
            self._pending[sequence_id] = _Pending(command=command)

    def resolve(self, sequence_id: str, command: str | None, payload: dict) -> bool:
        """Record an answer. Called from the MQTT thread.

        Only an armed sequence_id with the same ``command`` is taken, so a
        slicer's command that happens to reuse our sequence number on the
        shared report topic cannot answer for ours. Returns whether it matched.
        """
        with self._lock:
            entry = self._pending.get(str(sequence_id))
            if entry is None or entry.command != command or entry.payload is not None:
                return False
            entry.payload = payload
            return True

    def discard(self, sequence_id: str) -> None:
        with self._lock:
            self._pending.pop(str(sequence_id), None)

    def is_pending(self, sequence_id: str) -> bool:
        with self._lock:
            return str(sequence_id) in self._pending

    def answer(self, sequence_id: str) -> dict | None:
        """The answer recorded for an armed sequence_id, if any (not consumed)."""
        with self._lock:
            entry = self._pending.get(str(sequence_id))
            return entry.payload if entry else None

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def fail_all(self, status: AckStatus = AckStatus.DISCONNECTED) -> int:
        """End every unanswered wait with ``status`` — on disconnect, so callers
        are not left waiting out the full timeout. Returns how many were ended."""
        ended = 0
        with self._lock:
            for entry in self._pending.values():
                if entry.payload is None and entry.status is None:
                    entry.status = status
                    ended += 1
        return ended

    # -- waiting ----------------------------------------------------------

    async def wait(self, sequence_id: str, timeout: float = DEFAULT_ACK_TIMEOUT) -> AckResult:
        """Wait for the answer to an armed sequence_id; always disarms it."""
        sequence_id = str(sequence_id)
        deadline = time.monotonic() + timeout
        command = ""
        try:
            while True:
                with self._lock:
                    entry = self._pending.get(sequence_id)
                    payload = entry.payload if entry else None
                    ended = entry.status if entry else None
                    command = entry.command if entry else command
                if entry is None:
                    # Disarmed underneath us (discard); nothing will arrive.
                    return AckResult(AckStatus.TIMEOUT, command, sequence_id)
                if payload is not None:
                    status, reason = verdict(payload)
                    return AckResult(status, command, sequence_id, reason, payload)
                if ended is not None:
                    return AckResult(ended, command, sequence_id)
                if time.monotonic() >= deadline:
                    logger.info("[%s] No answer to %s seq=%s within %.1fs", self._label, command, sequence_id, timeout)
                    return AckResult(AckStatus.TIMEOUT, command, sequence_id)
                await asyncio.sleep(_POLL_INTERVAL)
        finally:
            self.discard(sequence_id)

    # -- capturing existing publish paths ---------------------------------

    def note_publish(self, sequence_id: str, command: str) -> None:
        """Hook for publish paths: arm ``sequence_id`` if a capture() is active.

        Lets an existing synchronous command method (returning bool) report
        the printer's answer via ``call`` without changing its signature.
        Outside a capture this does nothing, so unwatched commands leave no
        entries behind.
        """
        captured = _capture.get()
        if captured is None:
            return
        self.arm(sequence_id, command)
        captured.append((str(sequence_id), command))

    @contextmanager
    def capture(self) -> Iterator[list[tuple[str, str]]]:
        """Collect the (sequence_id, command) pairs note_publish arms in this block."""
        captured: list[tuple[str, str]] = []
        token = _capture.set(captured)
        try:
            yield captured
        finally:
            _capture.reset(token)

    async def call(
        self,
        send: Callable[..., Any],
        *args: Any,
        timeout: float = DEFAULT_ACK_TIMEOUT,
        **kwargs: Any,
    ) -> AckResult:
        """Run a command method and wait for the printer's answer to it.

        ``send`` returning a falsy value means nothing went out (NOT_SENT). A
        method that published nothing we can correlate is ACCEPTED but not
        confirmed. When it published several commands, all are awaited and
        the first refusal (else the first non-accepted status) is reported.
        """
        captured: list[tuple[str, str]] = []
        try:
            with self.capture() as captured:
                sent = send(*args, **kwargs)
        except BaseException:
            for seq, _ in captured:
                self.discard(seq)
            raise
        if not sent:
            for seq, _ in captured:
                self.discard(seq)
            return AckResult(AckStatus.NOT_SENT)
        if not captured:
            return AckResult(AckStatus.ACCEPTED)
        results = await asyncio.gather(*(self.wait(seq, timeout) for seq, _ in captured))
        for wanted in (AckStatus.REFUSED, AckStatus.DISCONNECTED, AckStatus.TIMEOUT):
            for result in results:
                if result.status == wanted:
                    return result
        return results[-1]
