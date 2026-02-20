from dataclasses import dataclass
from pathlib import Path

from drone_control.utils.time import build_session_id


@dataclass
class RuntimeContext:
    session_id: str
    session_file: Path
    latest_file: Path
    _seq: int = 0

    @classmethod
    def from_commands_dir(cls, commands_dir: Path) -> "RuntimeContext":
        session_id = build_session_id()
        session_file = commands_dir / f"session_{session_id}.jsonl"
        latest_file = commands_dir / "latest_command.json"
        return cls(session_id=session_id, session_file=session_file, latest_file=latest_file)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq
