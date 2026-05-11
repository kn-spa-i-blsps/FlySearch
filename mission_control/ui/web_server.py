import asyncio
import base64
import os
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, Set, Optional, Tuple

import uvicorn
from PIL import Image
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mission_control.core.action_status import ActionStatus
from mission_control.core.config import Config
from mission_control.core.events import (
    StartMissionCommand, CreateNewSessionCommand, PhotoWithTelemetryReceived,
    VlmAnalysisCompleted, AskUserConfirmationCommand, SearchEnded, UserDecisionReceived
)
from mission_control.core.interfaces import EventBus
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)


@dataclass
class MissionUIState:
    """ Stores the GUI state for one specific mission. """
    chat_history: list = field(default_factory=list)
    custom_status: str = "Waiting for mission to start..."
    last_photo_name: Optional[str] = None
    parsed_action: Optional[dict] = None
    waiting_for_decision: bool = False
    pending_move: Optional[Tuple] = None
    connected_websockets: Set[WebSocket] = field(default_factory=set)


# Model for the incoming POST request to start a new mission
class MissionCreateRequest(BaseModel):
    mission_id: str
    drone_id: str
    prompt_type: str
    search_object: str
    glimpses: int
    area: int
    min_altitude: int


class WebServer:
    def __init__(self, config: Config, event_bus: EventBus):
        self.config = config
        self.event_bus = event_bus
        self.app = FastAPI(title="Mission Control GUI")

        # --- MULTI-TENANT GUI STATE ---
        # Dictionary storing a separate browser window state for each mission_id
        self.missions: Dict[str, MissionUIState] = {}
        # Helper dictionary to track which mission a drone photo belongs to
        self.drone_to_mission: Dict[str, str] = {}

        # Mounting uploads for photo access.
        os.makedirs(self.config.upload_dir, exist_ok=True)
        self.app.mount("/uploads", StaticFiles(directory=self.config.upload_dir), name="uploads")

        # Endpoint registers.
        self.app.add_api_route("/", self.get_index, methods=["GET"])
        self.app.add_api_route("/api/missions", self.api_start_mission, methods=["POST"])
        self.app.add_api_route("/{mission_id}", self.get_mission_gui, methods=["GET"])
        self.app.add_api_websocket_route("/ws/{mission_id}", self.websocket_endpoint)

        # Place for the Uvicorn server.
        self.server = None

        # --- EVENT SUBSCRIPTIONS ---
        self.event_bus.subscribe(StartMissionCommand, self.handle_mission_start)
        self.event_bus.subscribe(CreateNewSessionCommand, self.handle_new_session)
        self.event_bus.subscribe(PhotoWithTelemetryReceived, self.handle_photo)
        self.event_bus.subscribe(VlmAnalysisCompleted, self.handle_vlm_analysis)
        self.event_bus.subscribe(AskUserConfirmationCommand, self.handle_ask_confirmation)
        self.event_bus.subscribe(SearchEnded, self.handle_search_ended)

    def _get_or_create_mission(self, mission_id: str) -> MissionUIState:
        if mission_id not in self.missions:
            self.missions[mission_id] = MissionUIState()
        return self.missions[mission_id]

    # ==========================================
    # EVENT BUS HANDLERS
    # ==========================================

    async def handle_mission_start(self, event: StartMissionCommand):
        # Map the drone to the mission so photos go to the correct window
        self.drone_to_mission[event.drone_id] = event.mission_id

        m_state = self._get_or_create_mission(event.mission_id)
        m_state.custom_status = f"Mission {event.mission_id} started."
        m_state.parsed_action = None
        m_state.waiting_for_decision = False
        await self.broadcast_state(event.mission_id)

    async def handle_new_session(self, event: CreateNewSessionCommand):
        m_state = self._get_or_create_mission(event.chat_id)
        m_state.chat_history = []
        m_state.chat_history.append({"role": "USER", "type": "text", "content": event.prompt})
        await self.broadcast_state(event.chat_id)

    async def handle_photo(self, event: PhotoWithTelemetryReceived):
        # Find out which mission this drone belongs to
        mission_id = self.drone_to_mission.get(event.drone_id)
        if not mission_id:
            return

        m_state = self._get_or_create_mission(mission_id)
        path_obj = Path(event.photo_path)
        m_state.last_photo_name = path_obj.name
        m_state.custom_status = "Analyzing new photo..."

        img_b64 = self._encode_image_to_base64(path_obj)
        m_state.chat_history.append({"role": "USER", "type": "image", "content": img_b64})
        await self.broadcast_state(mission_id)

    async def handle_vlm_analysis(self, event: VlmAnalysisCompleted):
        m_state = self._get_or_create_mission(event.chat_id)
        m_state.parsed_action = {"found": event.found, "move": event.move}
        m_state.chat_history.append({"role": "VLM", "type": "text", "content": event.reasoning})

        if event.found:
            m_state.custom_status = "OBJECT FOUND!"
        else:
            m_state.custom_status = f"VLM proposes move: {event.move}"

        await self.broadcast_state(event.chat_id)

    async def handle_ask_confirmation(self, event: AskUserConfirmationCommand):
        m_state = self._get_or_create_mission(event.mission_id)
        m_state.waiting_for_decision = True
        m_state.pending_move = event.move
        m_state.custom_status = "Waiting for human confirmation..."
        await self.broadcast_state(event.mission_id)

    async def handle_search_ended(self, event: SearchEnded):
        m_state = self._get_or_create_mission(event.mission_id)
        m_state.waiting_for_decision = False
        m_state.custom_status = f"Mission Ended. Success: {event.found}"
        await self.broadcast_state(event.mission_id)

    # ==========================================
    # WEBSOCKET & HTTP LOGIC (GUI <-> BACKEND)
    # ==========================================

    async def api_start_mission(self, req: MissionCreateRequest):
        """ Endpoint: POST /api/missions """
        if req.mission_id in self.missions:
            raise HTTPException(
                status_code=400,
                detail=f"Mission with ID '{req.mission_id}' already exists. Please choose a different name."
            )
        kv = {
            "object": req.search_object,
            "glimpses": str(req.glimpses),
            "minimum_altitude": str(req.min_altitude)
        }
        if req.prompt_type == "FS-1":
            kv["area"] = str(req.area)

        event = StartMissionCommand(
            mission_id=req.mission_id,
            drone_id=req.drone_id,
            prompt_type=req.prompt_type,
            prompt_args=kv
        )

        # Publish the event to the Orchestrator
        await self.event_bus.publish(event)

        # Respond with the URL to the new mission's dashboard
        return {"status": "ok", "url": f"/{req.mission_id}"}

    async def get_index(self):
        """ Endpoint: GET / (Mission Launcher & Active Missions GUI) """
        if self.missions:
            active_missions_html = '<ul class="mission-list">'
            for m_id, m_state in self.missions.items():
                status_color = "#27ae60" if m_state.waiting_for_decision else "#7f8c8d"
                active_missions_html += f'''
                    <li>
                        <a href="/{m_id}">
                            <div class="mission-title">Mission: {m_id}</div>
                            <div class="mission-status" style="color: {status_color}; font-size: 12px;">{m_state.custom_status}</div>
                        </a>
                    </li>
                '''
            active_missions_html += '</ul>'
        else:
            active_missions_html = '<p class="empty-state">No active missions running at the moment.</p>'
        # TODO: move to additional file
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>FlySearch - Mission Hub</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; display: flex; justify-content: center; align-items: flex-start; height: 100vh; margin: 0; padding-top: 50px; }}
                .container {{ display: flex; gap: 30px; align-items: flex-start; flex-wrap: wrap; justify-content: center; }}
                .card {{ background: #fff; padding: 30px; border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); width: 400px; }}
                h2 {{ color: #2c3e50; margin-top: 0; border-bottom: 2px solid #eee; padding-bottom: 10px; text-align: center; }}

                /* Form Styles */
                .form-group {{ margin-bottom: 15px; }}
                label {{ display: block; margin-bottom: 5px; font-weight: bold; color: #34495e; font-size: 14px; }}
                input, select {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box; font-size: 14px; }}
                button {{ width: 100%; padding: 12px; background: #0078D7; color: white; font-weight: bold; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; margin-top: 10px; transition: background 0.2s; }}
                button:hover {{ background: #005a9e; }}

                /* Mission List Styles */
                .mission-list {{ list-style: none; padding: 0; margin: 0; }}
                .mission-list li {{ background: #f8f9fa; margin-bottom: 10px; border-radius: 5px; border-left: 4px solid #0078D7; transition: transform 0.2s; }}
                .mission-list li:hover {{ transform: translateX(5px); background: #eef2f5; }}
                .mission-list a {{ text-decoration: none; color: #2c3e50; display: block; padding: 15px; }}
                .mission-title {{ font-weight: bold; font-size: 16px; margin-bottom: 5px; }}
                .empty-state {{ text-align: center; color: #7f8c8d; font-style: italic; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="card">
                    <h2>Launch New Mission</h2>
                    <form id="mission-form">
                        <div class="form-group">
                            <label>Mission ID</label>
                            <input type="text" id="mission_id" required placeholder="e.g., alpha_search">
                        </div>
                        <div class="form-group">
                            <label>Drone ID</label>
                            <input type="text" id="drone_id" required placeholder="e.g., drone_01">
                        </div>
                        <div class="form-group">
                            <label>Search Pattern (Prompt Type)</label>
                            <select id="prompt_type" onchange="toggleArea()">
                                <option value="FS-1">FS-1 (Area Search)</option>
                                <option value="FS-2">FS-2 (Path/Generic Search)</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Target Object</label>
                            <input type="text" id="search_object" required placeholder="e.g., lost hiker">
                        </div>
                        <div class="form-group">
                            <label>Max Moves (Glimpses)</label>
                            <input type="number" id="glimpses" value="10" required>
                        </div>
                        <div class="form-group" id="area-group">
                            <label>Search Area (m x m)</label>
                            <input type="number" id="area" value="80">
                        </div>
                        <div class="form-group">
                            <label>Min Altitude (m)</label>
                            <input type="number" id="min_altitude" value="10" required>
                        </div>
                        <button type="submit">Launch Mission</button>
                    </form>
                </div>

                <div class="card">
                    <h2>Active Missions</h2>
                    {active_missions_html}
                </div>
            </div>

            <script>
                function toggleArea() {{
                    const type = document.getElementById('prompt_type').value;
                    document.getElementById('area-group').style.display = type === 'FS-1' ? 'block' : 'none';
                }}

                document.getElementById('mission-form').addEventListener('submit', async (e) => {{
                    e.preventDefault(); 

                    const payload = {{
                        mission_id: document.getElementById('mission_id').value.trim(),
                        drone_id: document.getElementById('drone_id').value.trim(),
                        prompt_type: document.getElementById('prompt_type').value,
                        search_object: document.getElementById('search_object').value.trim(),
                        glimpses: parseInt(document.getElementById('glimpses').value),
                        area: parseInt(document.getElementById('area').value || 0),
                        min_altitude: parseInt(document.getElementById('min_altitude').value)
                    }};

                    const response = await fetch('/api/missions', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                    }});

                    if (response.ok) {{
                        const data = await response.json();
                        window.location.href = data.url;
                    }} else {{
                        const errorData = await response.json();
                        alert(`Error: ${{errorData.detail || 'Failed to launch mission.'}}`);
                    }}
                }});
            </script>
        </body>
        </html>
        """
        return HTMLResponse(html)

    async def websocket_endpoint(self, websocket: WebSocket, mission_id: str):
        """ Endpoint: WS /ws/{mission_id} """
        await websocket.accept()
        m_state = self._get_or_create_mission(mission_id)
        m_state.connected_websockets.add(websocket)

        # Immediately send the current state for this mission upon connection
        await self.broadcast_state(mission_id)

        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "decision":
                    decision_str = data.get("value")

                    if m_state.waiting_for_decision:
                        status = getattr(ActionStatus, decision_str)

                        decision_event = UserDecisionReceived(
                            mission_id=mission_id,
                            decision=status,
                            move=m_state.pending_move
                        )

                        m_state.waiting_for_decision = False
                        m_state.pending_move = None
                        m_state.custom_status = f"Decision '{decision_str}' sent."

                        await self.event_bus.publish(decision_event)
                        await self.broadcast_state(mission_id)

        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass
        finally:
            if websocket in m_state.connected_websockets:
                m_state.connected_websockets.remove(websocket)

    async def broadcast_state(self, mission_id: str):
        """ Sends the new status to all browsers observing this specific mission. """
        m_state = self.missions.get(mission_id)
        if not m_state or not m_state.connected_websockets:
            return

        state = {
            "type": "state_update",
            "waiting": m_state.waiting_for_decision,
            "custom_status": m_state.custom_status,
            "photo_path": m_state.last_photo_name,
            "parsed_action": m_state.parsed_action,
            "chat_history": m_state.chat_history,
            "pending_move": m_state.pending_move
        }

        for ws in list(m_state.connected_websockets):
            try:
                await ws.send_json(state)
            except Exception as e:
                logger.warning(f"Failed to send state to websocket: {e}")

    # ==========================================
    # HELPERS & SERVER
    # ==========================================

    def _encode_image_to_base64(self, path: Path) -> str:
        try:
            if not path.exists():
                return "[Error: Image file not found]"
            img = Image.open(path)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail((400, 400))
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=70)
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{img_str}"
        except Exception as e:
            return f"[Error loading image: {e}]"

    async def get_mission_gui(self, mission_id: str):
        """ Endpoint: GET /{mission_id} """

        if mission_id not in self.missions:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found or not started yet.")

        html_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>FlySearch Mission Control</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; display: flex; height: 100vh; background-color: #f4f7f6;}
                #left-panel { flex: 2; display: flex; flex-direction: column; border-right: 2px solid #ddd; background: #fff; }
                #right-panel { flex: 1; display: flex; flex-direction: column; padding: 20px; background: #fafafa; overflow-y: auto; }
                .chat-header { background: #2c3e50; color: white; padding: 15px; margin: 0; font-size: 18px; }
                .chat-header span { font-weight: normal; font-size: 14px; opacity: 0.8; margin-left: 10px; }
                #chat-history { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
                .message { max-width: 80%; padding: 12px 16px; border-radius: 15px; line-height: 1.4; word-wrap: break-word; font-size: 15px; }
                .message.user { align-self: flex-end; background-color: #0078D7; color: white; border-bottom-right-radius: 2px; }
                .message.vlm { align-self: flex-start; background-color: #e9ecef; color: #333; border-bottom-left-radius: 2px; }
                .message img { max-width: 100%; border-radius: 10px; margin-top: 5px; }
                .message-role { font-weight: bold; font-size: 0.8em; margin-bottom: 5px; opacity: 0.8; }
                h3 { color: #2c3e50; border-bottom: 2px solid #ddd; padding-bottom: 5px; }
                #image-view img { max-width: 100%; border-radius: 8px; border: 1px solid #ccc; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
                .status-box { background: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; }
                .parsed-action { font-size: 1.2em; font-family: monospace; color: #d35400; font-weight: bold; }
                .controls { display: none; flex-direction: column; gap: 10px; margin-top: 15px; }
                button { padding: 12px; font-size: 16px; font-weight: bold; border: none; border-radius: 5px; cursor: pointer; transition: 0.2s; }
                button:hover { opacity: 0.9; transform: scale(0.98); }
                .btn-accept { background: #27ae60; color: white; }
                .btn-warn { background: #f39c12; color: white; }
                .btn-stop { background: #c0392b; color: white; }

                .btn-back { display: inline-flex; align-items: center; justify-content: center; width: max-content; padding: 8px 15px; margin-bottom: 20px; background: #e0e6ed; color: #2c3e50; text-decoration: none; border-radius: 5px; font-weight: bold; font-size: 14px; transition: background 0.2s; }
                .btn-back:hover { background: #cbd4df; }
                .btn-back span { margin-right: 8px; font-size: 18px; line-height: 1;}

                .waiting-pulse { animation: pulse 2s infinite; }
                @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(39, 174, 96, 0.4); } 70% { box-shadow: 0 0 0 10px rgba(39, 174, 96, 0); } 100% { box-shadow: 0 0 0 0 rgba(39, 174, 96, 0); } }
            </style>
        </head>
        <body>
            <div id="left-panel">
                <h2 class="chat-header">Conversation with VLM <span id="mission-title"></span></h2>
                <div id="chat-history"></div>
            </div>

            <div id="right-panel">
                <a href="/" class="btn-back"><span>&larr;</span> Back to Hub</a>

                <div class="status-box">
                    <h3>VLM Action</h3>
                    <p>Status: <span id="vlm-action" class="parsed-action">Connecting to mission...</span></p>
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
                const missionId = window.location.pathname.replace(/^\\/|\\/$/g, '');
                document.getElementById('mission-title').innerText = "[ Mission: " + missionId + " ]";

                const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
                const ws = new WebSocket(protocol + window.location.host + '/ws/' + missionId);
                const chatContainer = document.getElementById('chat-history');

                ws.onmessage = function(event) {
                    const data = JSON.parse(event.data);
                    if (data.type === 'state_update') {

                        if (data.chat_history) renderChat(data.chat_history);

                        if (data.photo_path) {
                            const img = document.getElementById('drone-photo');
                            img.src = "/uploads/" + data.photo_path + "?t=" + new Date().getTime();
                            img.style.display = "block";
                        }

                        if (data.waiting && data.pending_move) {
                            document.getElementById('vlm-action').innerText = `Awaiting confirmation for move: [${data.pending_move}]`;
                        } else if (data.custom_status && !data.waiting) {
                            document.getElementById('vlm-action').innerText = data.custom_status;
                        } else if (data.parsed_action) {
                            if (data.parsed_action.found || data.parsed_action.move) {
                                let actionText = data.parsed_action.found ? "OBJECT FOUND!" : `MOVE: [${data.parsed_action.move}]`;
                                document.getElementById('vlm-action').innerText = actionText;
                            } else {
                                document.getElementById('vlm-action').innerText = "Waiting for VLM...";
                            }
                        }

                        document.getElementById('controls').style.display = data.waiting ? "flex" : "none";
                    }
                };

                function renderChat(history) {
                    chatContainer.innerHTML = '';
                    history.forEach(msg => {
                        const msgDiv = document.createElement('div');
                        const isUser = msg.role.toUpperCase() === 'USER';
                        msgDiv.className = `message ${isUser ? 'user' : 'vlm'}`;

                        const roleSpan = document.createElement('div');
                        roleSpan.className = 'message-role';
                        roleSpan.innerText = isUser ? 'GROUND CONTROL (You)' : 'VLM MODEL';
                        msgDiv.appendChild(roleSpan);

                        if (msg.type === 'text') {
                            const textNode = document.createElement('span');
                            textNode.innerText = msg.content;
                            msgDiv.appendChild(textNode);
                        } else if (msg.type === 'image') {
                            const imgNode = document.createElement('img');
                            imgNode.src = msg.content;
                            msgDiv.appendChild(imgNode);
                        }
                        chatContainer.appendChild(msgDiv);
                    });
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }

                function sendDecision(decision) {
                    ws.send(JSON.stringify({type: 'decision', value: decision}));
                    document.getElementById('controls').style.display = "none";
                    document.getElementById('vlm-action').innerText = "Sending data to drone/VLM...";
                }
                
                document.addEventListener('keydown', function(event) {
                const controls = document.getElementById('controls');
                if (controls.style.display === 'flex') {
                    if (event.key === 'Enter') {
                        sendDecision('CONFIRMED');
                    } else if (event.key.toLowerCase() === 'w') {
                        sendDecision('WARNING');
                    } else if (event.key.toLowerCase() === 'n' || event.key === 'Escape') {
                        sendDecision('CANCELLED');
                    }
                }
            });
            </script>
        </body>
        </html>
        """
        return HTMLResponse(html_content)

    async def serve(self, host="0.0.0.0", port=8000):
        config = uvicorn.Config(self.app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        await self.server.serve()

    def request_stop(self):
        if self.server:
            self.server.should_exit = True
