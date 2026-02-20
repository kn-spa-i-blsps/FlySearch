from drone_control.sensors.base import Sensor


class LidarSensor(Sensor):
    """Extension-ready LiDAR sensor (currently noop)."""

    name = "lidar"

    def health(self) -> dict[str, object]:
        return {"sensor": self.name, "implemented": False}

    def snapshot(self) -> dict[str, object]:
        return {}
