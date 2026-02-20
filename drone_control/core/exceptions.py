class DroneControlError(Exception):
    """Base exception for drone_control package."""


class SensorError(DroneControlError):
    """Raised when sensor acquisition fails."""


class CommandHandlingError(DroneControlError):
    """Raised when command handling fails."""
