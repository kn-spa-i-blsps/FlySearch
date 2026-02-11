class MissionControlError(Exception):
    """ Base for whole mission control module's errors. """
    pass

# --- Drone's Exceptions ---
class DroneError(MissionControlError):
    """ Base for drone's exceptions. """
    pass

class NoDroneConnectedError(DroneError):
    pass

class DroneCommandFailedError(DroneError):
    pass

# --- VLM's Exceptions ---
class VLMError(MissionControlError):
    """ Base for VLM's exceptions. """
    pass

class VLMConnectionError(VLMError):
    pass

class VLMParseError(VLMError):
    pass