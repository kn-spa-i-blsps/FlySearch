from enum import IntEnum


class ActionStatus(IntEnum):
    ERROR = -1
    CANCELLED = 0
    CONFIRMED = 1
    WARNING = 2