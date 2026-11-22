"""AckTracker: correlating MQTT commands with the printer's answers."""

import asyncio
import threading

import pytest

from backend.app.services.mqtt_ack import AckResult, AckStatus, AckTracker, verdict


class TestVerdict:
    def test_success(self):
        assert verdict({"result": "success", "reason": "success"}) == (AckStatus.ACCEPTED, "")

    def test_fail_any_case_with_reason(self):
        assert verdict({"result": "FAIL", "reason": "ERROR STATE"}) == (AckStatus.REFUSED, "ERROR STATE")

    def test_fail_without_reason(self):
        assert verdict({"result": "fail"}) == (AckStatus.REFUSED, "printer reported failure")

    def test_fail_carries_err_code(self):
        status, reason = verdict({"result": "fail", "reason": "busy", "err_code": 84033543})
        assert status == AckStatus.REFUSED
        assert reason == "busy (err_code 84033543)"

    def test_zero_err_code_is_not_reported(self):
        assert verdict({"result": "fail", "reason": "busy", "err_code": 0}) == (AckStatus.REFUSED, "busy")

    def test_unknown_result_is_not_a_refusal(self):
        assert verdict({"result": "ok", "reason": "queued"}) == (AckStatus.ACCEPTED, "queued")


class TestRegistry:
    def test_sequence_zero_cannot_be_armed(self):
        tracker = AckTracker()
        with pytest.raises(ValueError):
            tracker.arm("0", "pause")
        with pytest.raises(ValueError):
            tracker.arm("", "pause")

    def test_unarmed_answer_is_dropped(self):
        tracker = AckTracker()
        assert not tracker.resolve("7", "gcode_line", {"result": "success"})
        assert tracker.pending_count == 0

    def test_answer_for_another_command_is_ignored(self):
        """A slicer reusing our sequence number must not answer for us."""
        tracker = AckTracker()
        tracker.arm("7", "gcode_line")
        assert not tracker.resolve("7", "extrusion_cali_get", {"result": "fail"})
        assert tracker.answer("7") is None
        assert tracker.resolve("7", "gcode_line", {"result": "success"})

    def test_first_answer_wins(self):
        tracker = AckTracker()
        tracker.arm("7", "gcode_line")
        assert tracker.resolve("7", "gcode_line", {"result": "success"})
        assert not tracker.resolve("7", "gcode_line", {"result": "fail"})
        assert tracker.answer("7") == {"result": "success"}


class TestWait:
    @pytest.mark.asyncio
    async def test_accepted(self):
        tracker = AckTracker()
        tracker.arm("3", "gcode_line")
        tracker.resolve("3", "gcode_line", {"result": "success", "sequence_id": "3"})

        result = await tracker.wait("3", timeout=1)

        assert result.status == AckStatus.ACCEPTED
        assert result.confirmed
        assert result.command == "gcode_line" and result.sequence_id == "3"
        assert tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_refused_with_reason(self):
        tracker = AckTracker()
        tracker.arm("3", "print_option")
        tracker.resolve("3", "print_option", {"result": "fail", "reason": "not supported"})

        result = await tracker.wait("3", timeout=1)

        assert result.refused
        assert result.reason == "not supported"
        assert tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_timeout_disarms(self):
        tracker = AckTracker()
        tracker.arm("3", "gcode_line")

        result = await tracker.wait("3", timeout=0.1)

        assert result.status == AckStatus.TIMEOUT
        assert not result.confirmed and not result.refused
        assert not tracker.is_pending("3")
        # A late answer after the timeout is dropped, not stored.
        assert not tracker.resolve("3", "gcode_line", {"result": "success"})

    @pytest.mark.asyncio
    async def test_answer_from_another_thread(self):
        tracker = AckTracker()
        tracker.arm("9", "gcode_line")

        def mqtt_thread():
            tracker.resolve("9", "gcode_line", {"result": "success"})

        loop = asyncio.get_running_loop()
        loop.call_later(0.05, lambda: threading.Thread(target=mqtt_thread).start())

        result = await tracker.wait("9", timeout=2)
        assert result.status == AckStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_fail_all_ends_waits_early(self):
        tracker = AckTracker()
        tracker.arm("1", "gcode_line")
        tracker.arm("2", "print_option")
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, tracker.fail_all)

        started = loop.time()
        results = await asyncio.gather(tracker.wait("1", timeout=5), tracker.wait("2", timeout=5))

        assert [r.status for r in results] == [AckStatus.DISCONNECTED, AckStatus.DISCONNECTED]
        assert loop.time() - started < 1
        assert tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_concurrent_waits_get_their_own_answers(self):
        tracker = AckTracker()
        for seq in ("10", "11", "12"):
            tracker.arm(seq, "gcode_line")

        def answer_in_reverse():
            tracker.resolve("12", "gcode_line", {"result": "success"})
            tracker.resolve("11", "gcode_line", {"result": "fail", "reason": "eleven"})
            tracker.resolve("10", "gcode_line", {"result": "success", "reason": "ten"})

        asyncio.get_running_loop().call_later(0.05, answer_in_reverse)
        r10, r11, r12 = await asyncio.gather(*(tracker.wait(s, timeout=2) for s in ("10", "11", "12")))

        assert (r10.status, r10.reason) == (AckStatus.ACCEPTED, "ten")
        assert (r11.status, r11.reason) == (AckStatus.REFUSED, "eleven")
        assert r12.status == AckStatus.ACCEPTED
        assert tracker.pending_count == 0


class _FakeClient:
    """A command method that publishes through note_publish, like send_gcode."""

    def __init__(self, tracker: AckTracker, answers: dict[str, dict | None]):
        self.tracker = tracker
        self.answers = answers
        self.seq = 0
        self.connected = True

    def send(self, command: str, raise_on_publish: bool = False) -> bool:
        if not self.connected:
            return False
        self.seq += 1
        seq = str(self.seq)
        self.tracker.note_publish(seq, command)
        if raise_on_publish:
            raise RuntimeError("socket closed")
        answer = self.answers.get(command)
        if answer is not None:
            # The printer answers before the caller gets back to awaiting.
            self.tracker.resolve(seq, command, {"command": command, "sequence_id": seq, **answer})
        return True

    def send_two(self) -> bool:
        return self.send("airduct") and self.send("gcode_line")


class TestCall:
    @pytest.mark.asyncio
    async def test_accepted(self):
        tracker = AckTracker()
        client = _FakeClient(tracker, {"gcode_line": {"result": "success"}})

        result = await tracker.call(client.send, "gcode_line", timeout=1)

        assert result.confirmed
        assert result.sequence_id == "1"
        assert tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_refused(self):
        tracker = AckTracker()
        client = _FakeClient(tracker, {"gcode_line": {"result": "fail", "reason": "device busy"}})

        result = await tracker.call(client.send, "gcode_line", timeout=1)

        assert result.refused and result.reason == "device busy"

    @pytest.mark.asyncio
    async def test_timeout(self):
        tracker = AckTracker()
        client = _FakeClient(tracker, {})

        result = await tracker.call(client.send, "gcode_line", timeout=0.1)

        assert result.status == AckStatus.TIMEOUT
        assert tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_not_sent(self):
        tracker = AckTracker()
        client = _FakeClient(tracker, {})
        client.connected = False

        assert (await tracker.call(client.send, "gcode_line")).status == AckStatus.NOT_SENT

    @pytest.mark.asyncio
    async def test_publish_error_disarms_and_propagates(self):
        tracker = AckTracker()
        client = _FakeClient(tracker, {})

        with pytest.raises(RuntimeError):
            await tracker.call(client.send, "gcode_line", raise_on_publish=True)
        assert tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_nothing_correlatable_is_accepted_unconfirmed(self):
        tracker = AckTracker()

        result = await tracker.call(lambda: True)

        assert result == AckResult(AckStatus.ACCEPTED)
        assert not result.confirmed

    @pytest.mark.asyncio
    async def test_several_commands_report_the_refusal(self):
        tracker = AckTracker()
        client = _FakeClient(
            tracker, {"airduct": {"result": "success"}, "gcode_line": {"result": "fail", "reason": "no heater"}}
        )

        result = await tracker.call(client.send_two, timeout=1)

        assert result.refused and result.command == "gcode_line"
        assert tracker.pending_count == 0

    @pytest.mark.asyncio
    async def test_concurrent_calls_do_not_steal_answers(self):
        tracker = AckTracker()
        client = _FakeClient(tracker, {})

        async def call(command: str) -> AckResult:
            return await tracker.call(client.send, command, timeout=2)

        def answer():
            # Answer in reverse publish order.
            tracker.resolve("2", "print_option", {"result": "fail", "reason": "two"})
            tracker.resolve("1", "gcode_line", {"result": "success"})

        asyncio.get_running_loop().call_later(0.05, answer)
        first, second = await asyncio.gather(call("gcode_line"), call("print_option"))

        assert first.status == AckStatus.ACCEPTED and first.sequence_id == "1"
        assert second.refused and second.reason == "two" and second.sequence_id == "2"

    def test_note_publish_outside_capture_leaves_nothing(self):
        tracker = AckTracker()
        tracker.note_publish("5", "gcode_line")
        assert tracker.pending_count == 0
