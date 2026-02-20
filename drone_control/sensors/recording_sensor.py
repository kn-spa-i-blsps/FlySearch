from drone_control.sensors.base import Sensor


class RecordingSensor(Sensor):
    """Extension-ready camera recording sensor (currently noop)."""

    name = "recording"

    def __init__(self):
        self._recording = False

    def health(self) -> dict[str, object]:
        return {"sensor": self.name, "implemented": False, "recording": self._recording}

    def start_recording(self) -> bool:
        print("[RPi] RecordingSensor not implemented yet.")
        self._recording = False
        return False

    def stop_recording(self) -> bool:
        self._recording = False
        return False

    def status(self) -> dict[str, object]:
        return {"recording": self._recording}
