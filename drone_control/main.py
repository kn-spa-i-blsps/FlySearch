from drone_control.actuators.flight_controller import FlightController
from drone_control.bridges.server_bridge import ServerBridge
from drone_control.core.config import Config
from drone_control.core.runtime_context import RuntimeContext
from drone_control.managers.acquisition_manager import AcquisitionManager
from drone_control.managers.command_manager import CommandManager
from drone_control.managers.message_router import MessageRouter
from drone_control.managers.session_log_manager import SessionLogManager
from drone_control.sensors.lidar_sensor import LidarSensor
from drone_control.sensors.photo_sensor import PhotoSensor
from drone_control.sensors.recording_sensor import RecordingSensor
from drone_control.sensors.telemetry_sensor import TelemetrySensor


def build_server(argv: list[str] | None = None) -> ServerBridge:
    config = Config.from_cli(argv)

    runtime_context = RuntimeContext.from_commands_dir(config.commands_dir)
    session_logger = SessionLogManager(runtime_context)

    photo_sensor = PhotoSensor(
        img_dir=config.img_dir,
        file_name=config.fname,
        width=config.width,
        height=config.height,
        quality=config.quality,
        video_device=config.video_device,
    )

    telemetry_sensor = TelemetrySensor(
        mav_device=config.mav_device,
        mav_baud=config.mav_baud,
        timeout=config.telemetry_timeout,
    )

    # Extension-ready sensors created and kept available for future wiring.
    _recording_sensor = RecordingSensor()
    _lidar_sensor = LidarSensor()

    acquisition = AcquisitionManager(photo_sensor=photo_sensor, telemetry_sensor=telemetry_sensor)

    flight_controller = FlightController(
        exec_moves=config.exec_moves,
        move_method=config.move_method,
        mav_device=config.mav_device,
        mav_baud=config.mav_baud,
    )
    command_manager = CommandManager(logger=session_logger, flight_controller=flight_controller)

    router = MessageRouter(
        acquisition=acquisition,
        command_manager=command_manager,
        telemetry_template_path=config.telemetry_template,
    )

    return ServerBridge(config=config, router=router)


def main(argv: list[str] | None = None) -> None:
    server = build_server(argv)
    server.run()


if __name__ == "__main__":
    main()
