"""Unit tests for CommandManager — move dispatch and seq echoing."""
import pytest
from unittest.mock import MagicMock
from pathlib import Path

from drone_control.managers.command_manager import CommandManager
from drone_control.managers.session_log_manager import SessionLogManager
from drone_control.actuators.flight_controller import FlightController
from drone_control.core.runtime_context import RuntimeContext


@pytest.fixture()
def tmpdir_manager(tmp_path):
    """Returns (CommandManager, FlightController mock) with a real temp dir for logging."""
    ctx = RuntimeContext.from_commands_dir(tmp_path)
    logger = SessionLogManager(ctx)
    fc = MagicMock(spec=FlightController)
    fc.maybe_execute_move.return_value = True
    mgr = CommandManager(logger=logger, flight_controller=fc)
    return mgr, fc


class TestMoveCommand:
    def test_move_calls_flight_controller(self, tmpdir_manager):
        mgr, fc = tmpdir_manager
        mgr.handle_command({"type": "COMMAND", "action": "MOVE", "move": [1.0, 2.0, 3.0], "seq": 10})
        fc.maybe_execute_move.assert_called_once_with((1.0, 2.0, 3.0))

    def test_move_ack_echoes_seq(self, tmpdir_manager):
        mgr, fc = tmpdir_manager
        ack = mgr.handle_command({"type": "COMMAND", "action": "MOVE", "move": [0, 0, 1], "seq": 42})
        assert ack["seq"] == 42

    def test_move_ack_ok_true_when_fc_succeeds(self, tmpdir_manager):
        mgr, fc = tmpdir_manager
        fc.maybe_execute_move.return_value = True
        ack = mgr.handle_command({"type": "COMMAND", "action": "MOVE", "move": [0, 0, 1], "seq": 1})
        assert ack["ok"] is True
        assert ack["executed"] is True

    def test_move_ack_executed_false_when_fc_disabled(self, tmpdir_manager):
        mgr, fc = tmpdir_manager
        fc.maybe_execute_move.return_value = False
        ack = mgr.handle_command({"type": "COMMAND", "action": "MOVE", "move": [0, 0, 1], "seq": 1})
        assert ack["ok"] is True
        assert ack["executed"] is False

    def test_move_ack_includes_action(self, tmpdir_manager):
        mgr, fc = tmpdir_manager
        ack = mgr.handle_command({"type": "COMMAND", "action": "MOVE", "move": [1, 0, 0], "seq": 5})
        assert ack["action"] == "MOVE"


class TestFoundCommand:
    def test_found_returns_ok_ack(self, tmpdir_manager):
        mgr, _ = tmpdir_manager
        ack = mgr.handle_command({"type": "COMMAND", "action": "FOUND", "seq": 7})
        assert ack["ok"] is True
        assert ack["seq"] == 7

    def test_found_does_not_call_fc(self, tmpdir_manager):
        mgr, fc = tmpdir_manager
        mgr.handle_command({"type": "COMMAND", "action": "FOUND", "seq": 1})
        fc.maybe_execute_move.assert_not_called()


class TestUnknownCommand:
    def test_unknown_action_returns_none(self, tmpdir_manager):
        mgr, _ = tmpdir_manager
        result = mgr.handle_command({"type": "COMMAND", "action": "FLY_BACKWARDS", "seq": 1})
        assert result is None


class TestSeqHandling:
    def test_seq_none_when_not_provided(self, tmpdir_manager):
        mgr, _ = tmpdir_manager
        ack = mgr.handle_command({"type": "COMMAND", "action": "FOUND"})
        assert "seq" not in ack

    def test_seq_echoed_not_incremented(self, tmpdir_manager):
        """Server's seq must be echoed back verbatim, not replaced by the internal log seq."""
        mgr, _ = tmpdir_manager
        ack1 = mgr.handle_command({"type": "COMMAND", "action": "FOUND", "seq": 99})
        ack2 = mgr.handle_command({"type": "COMMAND", "action": "FOUND", "seq": 200})
        assert ack1["seq"] == 99
        assert ack2["seq"] == 200
