from enum import IntEnum, auto


class ActionStatus(IntEnum):
    """ Status of the move proposed by the VLM. """

    CANCELLED = auto()   # User canceled the search (stopped)
    CONFIRMED = auto()   # User confirmed move and it was successfully performed.
    WARNING = auto()     # User decided to not perform the move - it was dangerous.