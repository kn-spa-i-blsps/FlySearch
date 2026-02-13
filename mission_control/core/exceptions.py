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

class DroneConnectionLostError(DroneError):
    pass

class DroneAlreadyConnectedError(DroneError):
    pass

class DroneInvalidDataError(DroneError):
    pass

# --- VLM's Exceptions ---
class VLMError(MissionControlError):
    """ Base for VLM's exceptions. """
    pass

class VLMConnectionError(VLMError):
    pass

class VLMParseError(VLMError):
    pass

class VLMPreconditionsNotMetError(VLMError):
    """ Raised when preconditions for sending data to VLM are not met. """
    pass

# --- Chat's Exceptions ---
class ChatError(MissionControlError):
    """ Base for chat manager's exceptions. """
    pass

class ChatSessionError(ChatError):
    pass

class ChatSaveError(ChatError):
    pass

class ChatRestoreError(ChatError):
    pass

# --- Additional Exceptions ---
class ParsingError(ValueError):
    pass