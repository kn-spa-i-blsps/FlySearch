# FlySearch

> A Python-based evaluation pipeline connecting UAVs to Vision Language Models (Gemini, OpenAI) to benchmark AI effectiveness in aerial Search and Rescue (SAR) missions.

FlySearch is a research-oriented system designed to evaluate how well state-of-the-art VLMs can guide a drone during search operations. By analyzing real-time aerial imagery and MAVLink telemetry, the VLM generates relative movement vectors along with the reasoning, while the human operator retains final approval over flight execution.

---

## 1. System Architecture

The repository consists of two cooperating applications communicating via WebSockets:

* **`mission_control` (Laptop/Server):** The orchestration hub. It runs a CLI, manages the WebSocket server, queries the VLM API, and logs the mission data.
* **`drone_control` (Raspberry Pi/Producer):** The onboard companion computer. It captures camera feeds, reads MAVLink telemetry from the flight controller (Pixhawk), executes approved flight commands, and acts as a WebSocket client.

---

## 2. Prerequisites

Before starting, ensure both machines (Laptop and RPi) meet the following requirements:

* **Software:** Docker Engine + Docker Compose plugin installed on both machines.
* **VLM API Access:** An active API key for OpenAI (`OPEN_AI_KEY`) or Google Gemini (`GEMINI_AI_KEY`).
* **Hardware (RPi side):**
    * Camera device(s) available (`/dev/video*` or Picamera2 stack).
    * Flight Controller serial device available (default: `/dev/ttyAMA0` for MAVLink communication).
* **Optional Tools:**
    * `cloudflared` installed on the laptop (for testing over non-LAN networks).
    * `ffmpeg` available in the server runtime (for server-side video conversion).

---

## 3. Installation & Setup

### 1. Prepare Environment Files
Clone the repository and set up the `.env` files on **both** the laptop and the RPi:

```bash
cd docker
cp .env_example .env
```

**Laptop (Server) `.env` configuration:**
```dotenv
# Select your VLM provider
MODEL_BACKEND=openai      # or gemini
MODEL_NAME=oai-gpt-4o     # or gemini-2.5-flash
OPEN_AI_KEY=your_key_here # or GEMINI_AI_KEY=your_key_here

# Recording parameters
RECORDINGS_DIR=/recordings
PULL_BATCH_SIZE=2
PULL_CHUNK_BYTES=524288

# Camera FOV (degrees) of the square photo the grid overlay is drawn on.
# Default matches an Arducam M12 / Sony IMX477 rig's vertical FOV; if you're
# on different hardware, verify (and retune) with scripts/calibrate_grid_scale.py.
FOV_ANGLE=10.8
```

**Benchmarking against a self-hosted model (e.g. a LoRA finetune):** `MODEL_BACKEND=openai` talks to any OpenAI-compatible chat-completions endpoint, not just api.openai.com — set `OPENAI_BASE_URL` to point it at a self-hosted server instead (e.g. `vllm serve` exposing both a finetune and its base model on one URL, selected by `MODEL_NAME`):
```dotenv
MODEL_BACKEND=openai
MODEL_NAME=flylora                          # or e.g. Qwen/Qwen3.5-9B for the base model
OPENAI_BASE_URL=http://<vllm-host>:8000/v1
OPEN_AI_KEY=not-needed                       # vLLM ignores this unless you configured auth
```
To compare multiple models, restart `mission_control` between runs with a different `MODEL_BACKEND`/`MODEL_NAME`/`OPENAI_BASE_URL` in `.env` — there's no in-session model switch.

**Raspberry Pi (Producer) `.env` configuration:**
```dotenv
SERVER_URL=ws://<LAPTOP_IP>:8080 # Might need to use wss:// if using Cloudflare Tunnel
MAV_DEVICE=/dev/ttyAMA0
VIDEO_DEVICE=/dev/video0
RECORD_FPS=30
ENABLE_VIDEO=0 # Set to 1 only when video recording is working
```

### 2. Build Docker Images
Run this command on **both** machines to build the respective containers:

```bash
docker build -t flysearch:latest .
```

---

## 4. Running the System

### 1. Start Mission Server (Laptop)
Launch the orchestration CLI:

```bash
cd docker
docker compose --profile server run --rm --service-ports server
```
*You should see logs indicating the WebSocket server is listening on `0.0.0.0:8080`.*

> **Optional: Remote Access via Cloudflare**
> If your RPi and Laptop are not on the same LAN, open a new terminal on the laptop and run: `cloudflared tunnel --url http://localhost:8080/`. Update the RPi's `SERVER_URL` in `.env` with the generated `wss://` link.

### 2. Start Drone Producer (Raspberry Pi)
Connect the drone to the system:

```bash
cd docker
docker compose --profile producer up --build
```
*Look for `[WS] connected` on the laptop and `[RPi] WS open` on the RPi to confirm the connection.*

> **Test Mode (No Hardware):** Use the test profile on your RPi or Linux machine to mock camera and telemetry inputs:
> `docker compose --profile producer_test up --build`

---

## 5. Executing a FlySearch Mission

The core feature of this pipeline is the `SEARCH` command, run from the `mission_control` CLI on your laptop. 

### The `SEARCH` Command

```text
SEARCH <name> <FS-1|FS-2> object=<target> glimpses=<max_moves> [area=<meters>] minimum_altitude=<meters>
```

* **`FS-1` / `FS-2`**: Initial prompt strategy templates.
* **`glimpses`**: Maximum number of VLM inferences (moves) allowed.

**Example:**
```text
SEARCH test_run FS-1 object=helipad glimpses=6 area=80 minimum_altitude=10
```

### The Human-in-the-Loop (HITL) Flow
Once initiated, the system loops through the following sequence:
1. **Context Gathering:** The server requests a photo and MAVLink telemetry from the RPi.
2. **VLM Inference:** The image, telemetry, and the active prompt (`FS-1`/`FS-2`) are sent from the server to the AI model.
3. **Vector Proposal:** The VLM analyzes the scene and proposes a relative movement vector (e.g., `<x, y, z>`).
4. **Human Approval:** The CLI prompts you to review the proposed move:
    * `Enter` / `y`: Accept and send the MAVLink move command to the drone.
    * `n`: Reject, abort the move, and stop.
    * `w`: Send a collision/safety warning to the VLM and request a recalculation.
5. **Termination:** The loop ends when the object is found, the `glimpses` limit is reached, or the user cancels.

---

## 6. Graphical User Interface (GUI)

In addition to the command-line interface, FlySearch features a user-friendly Graphical User Interface (GUI) designed to streamline the `SEARCH` mission monitoring and the Human-in-the-Loop (HITL) workflow.

The GUI acts as a visual dashboard where operators can interact with the VLM and oversee the drone's decision-making process. 

**Key Features of the GUI:**
* **Live VLM Chat Interface:** A real-time timeline displaying the ongoing conversation with the Vision Language Model.
* **Visual Context & Telemetry:** Directly view the captured aerial photos and the exact telemetry data that were sent to the VLM alongside the initial prompts.
* **Transparent Reasoning:** Read the VLM's scene analysis, object detection reasoning, and its proposed relative movement vectors.
* **Interactive HITL Controls:** Dedicated action buttons to manage the drone's next move directly from the interface:
    * **Accept:** Approves the VLM's proposed trajectory and immediately sends the MAVLink move command to the drone.
    * **Warn:** Rejects the current path and sends a safety/collision warning back to the VLM, prompting it to recalculate a safer route based on the new context.
    * **Cancel:** Aborts the proposed move and terminates the current search loop.

The GUI can be accessed in your browser by navigating to ```http://127.0.0.1:8000```.

---

## 7. CLI Reference & Data Management

You can also run individual commands in the `mission_control` CLI to manually control the flow:

| Command | Description |
| :--- | :--- |
| `PROMPT ...` | Generates a prompt based on `FS-1`/`FS-2` strategies. |
| `CHAT_INIT` | Initializes the VLM chat context (requires `PROMPT` first). |
| `PHOTO_WITH_TELEMETRY` | Captures and caches current image + MAVLink telemetry. |
| `SEND_TO_VLM` | Sends cached photo + telemetry to the VLM for analysis. |
| `MOVE` | Executes the previously generated relative movement vector. |
| `START_RECORDING` | Starts saving `.h264` video on the RPi. |
| `GET_RECORDINGS` | Lists available `.h264` files on the RPi. |
| `PULL_RECORDINGS <file>` | Streams video files to the server and converts them to `.mp4`. |

### Generated Artifacts
All mission data is safely logged on the laptop (server side) for post-flight analysis:
* `uploads/` - Captured images from the drone.
* `telemetry/` - JSON logs of MAVLink data at each step.
* `saved_chats/` - Full VLM conversation history.
* `prompts/` - Prompt text and metadata.
* `recordings/` - Pulled raw video (`/raw`), metadata (`/meta`), and converted formats (`/mp4`).

---

## 8. Diagnostics & Calibration Tools

Two standalone scripts under `scripts/` let you sanity-check the physical setup before trusting a mission. Neither is part of the automated test suite — both talk to real hardware/artifacts and are meant to be run manually. Run them from the repo root with `python3 -m scripts.<name>`.

### Movement accuracy check
`scripts/check_movement_accuracy.py` commands the real vehicle (or a SITL instance) 1m forward, backward, left, and right in turn, and compares the *actual* displacement (read from MAVLink `LOCAL_POSITION_NED`) against what was commanded. The four legs cancel out, so a fully successful run returns the drone to its start point.

```bash
# status/dry-run only — connects, prints vehicle mode/armed state, sends nothing:
python3 -m scripts.check_movement_accuracy --device /dev/ttyAMA0

# actually move the vehicle:
python3 -m scripts.check_movement_accuracy --device /dev/ttyAMA0 --execute

# against SITL instead of real hardware:
python3 -m scripts.check_movement_accuracy --device udp:127.0.0.1:14550 --execute
```
It refuses to send anything unless the vehicle reports `GUIDED` + armed, and asks for a `y/N` confirmation before every leg. Run `--help` for all options (distance, tolerance, movement method, timeouts).

### Grid scale calibration
`scripts/calibrate_grid_scale.py` checks whether the meter-distance grid overlay the VLM sees (`mission_control/utils/add_guardrails.py`) actually matches reality for your camera. It works from an already-captured photo of a physical ground reference (e.g. two marks on a tape measure) taken at a known height — no drone/MAVLink connection needed.

```bash
# 1) crop & preview the photo the way the mission pipeline does:
python3 -m scripts.calibrate_grid_scale --photo shot.jpg --prepare
# -> writes shot.square.png; open it and note the pixel coords of two points
#    a known real-world distance apart.

# 2) run the calibration:
python3 -m scripts.calibrate_grid_scale --photo shot.jpg --height 20 \
    --ref-pixel1 120,340 --ref-pixel2 480,340 --ref-distance 5.0
```
It prints the predicted vs. measured meters-per-pixel scale, the error percentage, and a suggested `FOV_ANGLE`/`camera_fov_degrees` value, and writes `shot.grid_check.png` with the production overlay burned in for a visual check.

---

## 9. Running Tests

The offline unit test suite (no hardware, no camera) covers command parsing, chat/prompt management, the VLM/drone bridges, and the grid-overlay scale math:

```bash
pip install -r requirements.txt
pytest mission_control drone_control
```
