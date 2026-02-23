from abc import ABC, abstractmethod

class Actuator(ABC):
    """Define a base class for all actuator modules"""
    name: str

    @abstractmethod
    def health(self) -> dict[str, object]:
        """Return health status of this actuator"""

