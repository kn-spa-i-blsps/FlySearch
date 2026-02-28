import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    """Builds runtime configuration from CLI args + env vars"""
    server: str
    width: int
    height: int
    quality: int
    video_device: str
    video_dir: str
    record_fps: int
    commands_dir: Path
    telemetry_template: Path
    mav_device: str
    mav_baud: int
    telemetry_timeout: float
    move_method: int
    exec_moves: bool

    @classmethod
    def from_cli(cls, argv: Optional[list[str]] = None) -> "Config":
        parser = argparse.ArgumentParser()
        parser.add_argument("--server", default=os.environ.get("SERVER_URL", "ws://127.0.0.1:8080"))
        parser.add_argument("--width", default=int(os.environ.get("WIDTH", "480")), type=int)
        parser.add_argument("--height", default=int(os.environ.get("HEIGHT", "480")), type=int)
        parser.add_argument("--quality", default=int(os.environ.get("QUALITY", "90")), type=int)
        parser.add_argument("--video_device", default=os.environ.get("VIDEO_DEVICE", "/dev/video0"))
        parser.add_argument("--video_dir", default=os.environ.get("VIDEO_DIR", "/video"))
        parser.add_argument("--record_fps", default=int(os.environ.get("RECORD_FPS", "30")), type=int)
        parser.add_argument("--commands", default=os.environ.get("COMMANDS_DIR", "/commands"))
        parser.add_argument("--mav_device", default=os.environ.get("MAV_DEVICE", "/dev/ttyAMA0"))
        parser.add_argument("--mav_baud", default=int(os.environ.get("MAV_BAUD", "57600")), type=int)
        parser.add_argument("--telemetry_timeout", default=float(os.environ.get("TELEM_TIMEOUT", "2.0")), type=float)
        parser.add_argument("--move_method", default=int(os.environ.get("MOVE_METHOD", "0")), type=int)
        parser.add_argument("--exec_moves", default=int(os.environ.get("EXECUTE_MOVES", "1")), type=int)
        parser.add_argument("--telemetry_template", default=os.environ.get("TELEMETRY_TEMPLATE", "telemetry.json"))

        args = parser.parse_args(argv)

        cfg = cls(
            server=args.server,
            width=args.width,
            height=args.height,
            quality=args.quality,
            video_device=args.video_device,
            video_dir=args.video_dir,
            record_fps=args.record_fps,
            commands_dir=Path(args.commands),
            telemetry_template=Path(args.telemetry_template),
            mav_device=args.mav_device,
            mav_baud=args.mav_baud,
            telemetry_timeout=args.telemetry_timeout,
            move_method=args.move_method,
            exec_moves=bool(args.exec_moves),
        )
        cfg.ensure_directories()
        return cfg

    def ensure_directories(self) -> None:
        self.commands_dir.mkdir(parents=True, exist_ok=True)
