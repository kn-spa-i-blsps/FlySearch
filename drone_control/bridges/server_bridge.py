import websocket

from drone_control.core.config import Config
from drone_control.managers.message_router import MessageRouter


class ServerBridge:
    """Server bridge class for WS transport."""
    def __init__(self, *, config: Config, router: MessageRouter):
        self.config = config
        self.router = router

    def run(self) -> None:
        """Build and start the WebSocket client connection from RPi (drone_control)  to mission server (mission_control)."""
        ws = websocket.WebSocketApp(
            self.config.server,
            on_open=lambda _ws: print("[RPi] WS open"),
            on_error=lambda _ws, err: print(f"[RPi] WS error: {err}"),
            on_close=lambda _ws, code, msg: print(f"[RPi] WS closed code={code} msg={msg}"),
            on_data=lambda _ws, data, opcode, fin: print(
                f"[RPi] on_data: {'text' if opcode == 1 else 'binary' if opcode == 2 else opcode}, len={len(data)}"
            ),
            on_message=self.router.on_message, # Incoming messages are delegated to MessageRouter.on_message for actual command handling
        )
        ws.run_forever()
