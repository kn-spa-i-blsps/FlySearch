from drone_control.core.runtime_context import RuntimeContext
from drone_control.utils.files import append_jsonl, write_json_atomic
from drone_control.utils.time import now_ts


class SessionLogManager:
    """Handles persistent command logging."""
    def __init__(self, runtime_context: RuntimeContext):
        self.runtime_context = runtime_context

    def next_seq(self) -> int:
        return self.runtime_context.next_seq()

    def store_found(self, seq: int) -> dict[str, object]:
        normalized: dict[str, object] = {"ts": now_ts(), "seq": seq, "type": "FOUND"}
        self._store(normalized)
        return normalized

    def store_move(self, seq: int, move: tuple[float, float, float]) -> dict[str, object]:
        normalized: dict[str, object] = {
            "ts": now_ts(),
            "seq": seq,
            "type": "MOVE",
            "move": [float(move[0]), float(move[1]), float(move[2])],
        }
        self._store(normalized)
        return normalized

    def _store(self, normalized: dict[str, object]) -> None:
        append_jsonl(self.runtime_context.session_file, normalized)
        write_json_atomic(self.runtime_context.latest_file, normalized)
