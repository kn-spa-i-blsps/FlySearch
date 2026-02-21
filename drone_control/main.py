from drone_control.actuators.flight_controller import FlightController
from drone_control.bridges.server_bridge import ServerBridge
from drone_control.core.config import Config
from drone_control.core.runtime_context import RuntimeContext
from drone_control.managers.acquisition_manager import AcquisitionManager
from drone_control.managers.command_manager import CommandManager
from drone_control.managers.message_router import MessageRouter
from drone_control.managers.session_log_manager import SessionLogManager
from drone_control.sensors.photo_sensor import PhotoSensor
from drone_control.sensors.recording_sensor import RecordingSensor
from drone_control.sensors.telemetry_sensor import TelemetrySensor

class DroneControl:
    def __init__(self, argv: list[str] | None = None):
        self.config = Config.from_cli(argv)

        self.runtime_context = RuntimeContext.from_commands_dir(self.config.commands_dir)
        self.session_logger = SessionLogManager(self.runtime_context)

        self.photo_sensor = PhotoSensor(
            width=self.config.width,
            height=self.config.height,
            quality=self.config.quality,
            video_device=self.config.video_device,
        )

        self.telemetry_sensor = TelemetrySensor(
            mav_device=self.config.mav_device,
            mav_baud=self.config.mav_baud,
            timeout=self.config.telemetry_timeout,
            telemetry_template_path=self.config.telemetry_template,
        )

        # Extension-ready sensors kept available for future wiring.
        self.recording_sensor = RecordingSensor()

        self.acquisition = AcquisitionManager(
            photo_sensor=self.photo_sensor,
            telemetry_sensor=self.telemetry_sensor,
        )

        self.flight_controller = FlightController(
            exec_moves=self.config.exec_moves,
            move_method=self.config.move_method,
            mav_device=self.config.mav_device,
            mav_baud=self.config.mav_baud,
        )
        self.command_manager = CommandManager(
            logger=self.session_logger,
            flight_controller=self.flight_controller,
        )

        self.router = MessageRouter(
            acquisition=self.acquisition,
            command_manager=self.command_manager,
            telemetry_template_path=self.config.telemetry_template,
        )
        self.server = ServerBridge(config=self.config, router=self.router)

    def run(self) -> None:
        self.server.run()


def build_server(argv: list[str] | None = None) -> ServerBridge:
    # Compatibility wrapper for existing imports.
    return DroneControl(argv).server


def main(argv: list[str] | None = None) -> None:
    drone_control = DroneControl(argv)
    drone_control.run()


if __name__ == "__main__":
    main()
