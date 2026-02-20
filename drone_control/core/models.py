from dataclasses import dataclass
from typing import Optional


@dataclass
class MoveVector:
    x: float
    y: float
    z: float

    def as_tuple(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z


@dataclass
class CommandAck:
    ok: bool
    seq: Optional[int] = None
    executed: bool = False
    error: Optional[str] = None
