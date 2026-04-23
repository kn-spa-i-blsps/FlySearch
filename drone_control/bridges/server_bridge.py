import json

import websocket

from drone_control.core.config import Config
from drone_control.managers.message_router import MessageRouter


class ServerBridge:
    """Server bridge class for WS transport."""
    def __init__(self, *, config: Config, router: MessageRouter):
        self.config = config
        self.router = router
        self._authenticated = False # flag that tracks whether the drone successfully sent the AUTH handshake to server

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

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        """Send AUTH immediately after the connection is established."""
        self._authenticated = False
        auth_payload = json.dumps({"type": "AUTH", "drone_id": self.config.drone_id})
        ws.send(auth_payload)
        print(f"[RPi] WS open - AUTH sent (drone_id={self.config.drone_id})")

    def _on_message(self, ws: websocket.WebSocketApp, message) -> None:
        """Handle incoming messages.

        The first message must be the AUTH ACK from the server.
        All subsequent messages are delegated to the MessageRouter.
        """
        if not self._authenticated:
            try:
                obj = json.loads(message)
                if obj.get("type") == "ACK" and obj.get("of") == "AUTH":
                    if obj.get("ok"):
                        self._authenticated = True
                        print("[RPi] AUTH ACK received - connected")
                    else:
                        print("[RPi] AUTH rejected by server. Closing connection.")
                        self._close_with_reason(ws, status=1008, reason="AUTH rejected")
                else:
                    print(f"[RPi] Unexpected message before AUTH ACK: {message[:100]}")
            except Exception as exc:
                print(f"[RPi] Error handling pre-auth message: {exc}")
            return

        self.router.on_message(ws, message)

    def run(self) -> None:
        """Build and start the WebSocket client connection from RPi (drone_control) to mission server (mission_control)."""
        ws = websocket.WebSocketApp(
            self.config.server,
            on_open=self._on_open,
            on_error=lambda _ws, err: print(f"[RPi] WS error: {err}"),
            on_close=lambda _ws, code, msg: print(f"[RPi] WS closed code={code} msg={msg}"),
            on_message=self._on_message,
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
