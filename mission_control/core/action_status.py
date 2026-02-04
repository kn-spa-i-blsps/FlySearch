from enum import IntEnum


class ActionStatus(IntEnum):
    """ Status of the move proposed by the VLM. """

    ERROR = -1      # Error occurred (so user didn't even interact)
    CANCELLED = 0   # User canceled the search (stopped)
    CONFIRMED = 1   # User confirmed move and it was successfully performed.
    WARNING = 2     # User decided to not perform the move - it was dangerous.