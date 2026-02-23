from abc import ABC, abstractmethod
from typing import Any


class Sensor(ABC):
    name: str

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return current health metadata of this sensor."""
