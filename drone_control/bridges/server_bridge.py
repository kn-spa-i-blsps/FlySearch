import websocket

from drone_control.core.config import Config
from drone_control.managers.message_router import MessageRouter


class ServerBridge:
    """Server bridge class for WS transport."""
    def __init__(self, *, config: Config, router: MessageRouter):
        self.config = config
        self.router = router

    @staticmethod
    def _close_with_reason(ws: websocket.WebSocketApp, *, status: int, reason: str) -> None:
        """
        Try to close with a WS close frame (status/reason).
        Falls back to plain close for older websocket-client APIs.
        """
        try:
            ws.close(status=status, reason=reason)
        except TypeError:
            ws.close()
        except Exception as exc:
            print(f"[RPi] WS close failed: {exc}")

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
        try:
            ws.run_forever()
        except KeyboardInterrupt:
            print("[RPi] Ctrl+C received, closing WS gracefully...")
            self._close_with_reason(
                ws,
                status=1001,
                reason="RPi shutdown (Ctrl+C)",
            )
