# plik: mission_control/web_server.py
import asyncio
import base64
import os
from io import BytesIO

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from mission_control.core.action_status import ActionStatus


class WebServer:
    def __init__(self, mission_context):
        self.mission_context = mission_context

        self.connected_websockets = set()

        self.app = FastAPI(title="Mission Control GUI")

        # Mounting uploads for photo access.
        if not os.path.exists("uploads"):
            os.makedirs("uploads")
        self.app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

        # Endpoint registers.
        self.app.add_api_route("/", self.get_index, methods=["GET"])
        self.app.add_api_websocket_route("/ws", self.websocket_endpoint)

        # Place for the Uvicorn server.
        self.server = None

    async def get_index(self):
        html_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>FlySearch Mission Control</title>
            <style>
                body { 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    margin: 0; padding: 0; display: flex; height: 100vh; background-color: #f4f7f6;
                }
                /* Main Layout */
                #left-panel { flex: 2; display: flex; flex-direction: column; border-right: 2px solid #ddd; background: #fff; }
                #right-panel { flex: 1; display: flex; flex-direction: column; padding: 20px; background: #fafafa; overflow-y: auto; }
        
                /* Chat */
                .chat-header { background: #2c3e50; color: white; padding: 15px; margin: 0; font-size: 18px; }
                #chat-history { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
        
                .message { max-width: 80%; padding: 12px 16px; border-radius: 15px; line-height: 1.4; word-wrap: break-word; font-size: 15px; }
        
                .message.user { align-self: flex-end; background-color: #0078D7; color: white; border-bottom-right-radius: 2px; }
                .message.vlm { align-self: flex-start; background-color: #e9ecef; color: #333; border-bottom-left-radius: 2px; }
        
                .message img { max-width: 100%; border-radius: 10px; margin-top: 5px; }
                .message-role { font-weight: bold; font-size: 0.8em; margin-bottom: 5px; opacity: 0.8; }
        
                /* Right Panel - Data and Controls */
                h3 { color: #2c3e50; border-bottom: 2px solid #ddd; padding-bottom: 5px; }
                #image-view img { max-width: 100%; border-radius: 8px; border: 1px solid #ccc; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        
                .status-box { background: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; }
                .parsed-action { font-size: 1.2em; font-family: monospace; color: #d35400; font-weight: bold; }
        
                /* Buttons */
                .controls { display: none; flex-direction: column; gap: 10px; margin-top: 15px; }
                button { padding: 12px; font-size: 16px; font-weight: bold; border: none; border-radius: 5px; cursor: pointer; transition: 0.2s; }
                button:hover { opacity: 0.9; transform: scale(0.98); }
                .btn-accept { background: #27ae60; color: white; }
                .btn-warn { background: #f39c12; color: white; }
                .btn-stop { background: #c0392b; color: white; }
        
                /* Waiting animation */
                .waiting-pulse { animation: pulse 2s infinite; }
                @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(39, 174, 96, 0.4); } 70% { box-shadow: 0 0 0 10px rgba(39, 174, 96, 0); } 100% { box-shadow: 0 0 0 0 rgba(39, 174, 96, 0); } }
            </style>
        </head>
        <body>
        
            <div id="left-panel">
                <h2 class="chat-header">Conversation with VLM</h2>
                <div id="chat-history">
                    </div>
            </div>
        
            <div id="right-panel">
                <div class="status-box">
                    <h3>VLM Action</h3>
                    <p>Status: <span id="vlm-action" class="parsed-action">Waiting for search method to start...</span></p>
        
                    <div id="controls" class="controls">
                        <button class="btn-accept waiting-pulse" onclick="sendDecision('CONFIRMED')">Accept move (Enter)</button>
                        <button class="btn-warn" onclick="sendDecision('WARNING')">Send warning (W)</button>
                        <button class="btn-stop" onclick="sendDecision('CANCELLED')">Stop (No)</button>
                    </div>
                </div>
        
                <div id="image-view">
                    <h3>Current photo</h3>
                    <img id="drone-photo" src="" alt="No photo" style="display:none;"/>
                </div>
            </div>
        
            <script>
                const ws = new WebSocket(`ws://${window.location.host}/ws`);
                const chatContainer = document.getElementById('chat-history');
        
                ws.onmessage = function(event) {
                    const data = JSON.parse(event.data);
                    if (data.type === 'state_update') {
        
                        // 1. Chat update
                        if (data.chat_history) {
                            renderChat(data.chat_history);
                        }
        
                        // 2. Right panel update (photo)
                        if (data.photo_path) {
                            const img = document.getElementById('drone-photo');
                            img.src = "/uploads/" + data.photo_path + "?t=" + new Date().getTime();
                            img.style.display = "block";
                        }
        
                        // 3. Parsed action update or Custom status
                        if (data.custom_status) {
                            document.getElementById('vlm-action').innerText = data.custom_status;
                        } else if (data.parsed_action) {
                            if (data.parsed_action.found || data.parsed_action.move) {
                                let actionText = data.parsed_action.found ? "OBJECT FOUND!" : `MOVE: [${data.parsed_action.move}]`;
                                document.getElementById('vlm-action').innerText = actionText;
                            } else {
                                document.getElementById('vlm-action').innerText = "Waiting for VLM...";
                            }
                        }
        
                        // 4. Show/hide buttons
                        document.getElementById('controls').style.display = data.waiting ? "flex" : "none";
                    }
                };
        
                function renderChat(history) {
                    chatContainer.innerHTML = ''; // Clear current chat
        
                    history.forEach(msg => {
                        const msgDiv = document.createElement('div');
                        // Assign CSS class: 'user' for user, 'vlm' for the rest (e.g., assistant/system)
                        const isUser = msg.role.toUpperCase() === 'USER';
                        msgDiv.className = `message ${isUser ? 'user' : 'vlm'}`;
        
                        const roleSpan = document.createElement('div');
                        roleSpan.className = 'message-role';
                        roleSpan.innerText = isUser ? 'GROUND CONTROL (You)' : 'VLM MODEL';
                        msgDiv.appendChild(roleSpan);
        
                        if (msg.type === 'text') {
                            // Display text (preserve line breaks)
                            const textNode = document.createElement('span');
                            textNode.innerText = msg.content;
                            msgDiv.appendChild(textNode);
                        } else if (msg.type === 'image') {
                            // Display Base64 encoded image
                            const imgNode = document.createElement('img');
                            imgNode.src = msg.content;
                            msgDiv.appendChild(imgNode);
                        }
        
                        chatContainer.appendChild(msgDiv);
                    });
        
                    // Auto-scroll to the bottom
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }
        
                function sendDecision(decision) {
                    ws.send(JSON.stringify({type: 'decision', value: decision}));
                    document.getElementById('controls').style.display = "none";
                    document.getElementById('vlm-action').innerText = "Sending data to drone/VLM...";
                }
            </script>
        </body>
        </html>
        """
        return HTMLResponse(html_content)

    async def websocket_endpoint(self, websocket: WebSocket):
        """ Connects backend with frontend.

            Listens for the decision made by GUI user.
        """
        await websocket.accept()
        self.connected_websockets.add(websocket)
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "decision":
                    decision_str = data.get("value")
                    status = getattr(ActionStatus, decision_str)

                    # If system is waiting for the decision.
                    if self.mission_context.current_decision_future and not self.mission_context.current_decision_future.done():
                        self.mission_context.current_decision_future.set_result(status)

        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass
        finally:
            if websocket in self.connected_websockets:
                self.connected_websockets.remove(websocket)

    async def broadcast_state(self, waiting_for_decision=False, custom_status=None):
        """ Sends the new status to all browsers.

            :param: waiting_for_decision: If false, buttons are not displayed.
        """
        if not self.connected_websockets:
            return

        chat_history_payload = []
        if self.mission_context.conversation:
            history = self.mission_context.conversation.get_conversation()
            for role, content in history:
                role_str = str(role.name if hasattr(role, 'name') else role).replace("Role.", "")
                if isinstance(content, str):
                    chat_history_payload.append({"role": role_str, "type": "text", "content": content})
                else:
                    try:
                        buffered = BytesIO()
                        if content.mode in ("RGBA", "P"):
                            content = content.convert("RGB")
                        content.thumbnail((400, 400))
                        content.save(buffered, format="JPEG", quality=70)
                        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
                        chat_history_payload.append(
                            {"role": role_str, "type": "image", "content": f"data:image/jpeg;base64,{img_str}"})
                    except Exception as e:
                        chat_history_payload.append({"role": role_str, "type": "text", "content": f"[Error: {e}]"})

        parsed = self.mission_context.parsed_response
        last_photo = self.mission_context.last_photo_path_cache

        state = {
            "type": "state_update",
            "waiting": waiting_for_decision,
            "custom_status": custom_status,
            "photo_path": str(last_photo).split("/")[-1] if last_photo else None,
            "parsed_action": {
                "found": parsed.found if parsed else False,
                "move": parsed.move if parsed else None
            } if parsed else None,
            "chat_history": chat_history_payload
        }

        for ws in self.connected_websockets:
            await ws.send_json(state)

    async def serve(self, host="0.0.0.0", port=8000):
        """ Starts Uvicorn server in the background."""
        config = uvicorn.Config(self.app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        await self.server.serve()

    def request_stop(self):
        """ Sets soft exit flag for the server."""
        if self.server:
            self.server.should_exit = True