import websocket

from drone_control.core.config import Config
from drone_control.managers.message_router import MessageRouter


class ServerBridge:
    def __init__(self, *, config: Config, router: MessageRouter):
        self.config = config
        self.router = router

    def run(self) -> None:
        ws = websocket.WebSocketApp(
            self.config.server,
            on_open=lambda _ws: print("[RPi] WS open"),
            on_error=lambda _ws, err: print(f"[RPi] WS error: {err}"),
            on_close=lambda _ws, code, msg: print(f"[RPi] WS closed code={code} msg={msg}"),
            on_data=lambda _ws, data, opcode, fin: print(
                f"[RPi] on_data: {'text' if opcode == 1 else 'binary' if opcode == 2 else opcode}, len={len(data)}"
            ),
            on_message=self.router.on_message,
        )
        ws.run_forever()
