from abc import ABC, abstractmethod


class Actuator(ABC):
    name: str

    @abstractmethod
    def health(self) -> dict[str, object]:
        """Return current health metadata of this actuator."""
