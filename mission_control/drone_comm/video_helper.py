import logging

logger = logging.getLogger(__name__)

class VideoHelper:
    def __init__(self, storage, event_bus):
        self.storage = storage
        self.event_bus = event_bus