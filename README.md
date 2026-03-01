# flysearch

This repository runs two cooperating apps:

- `mission_control` (laptop/server side): CLI orchestration + VLM calls + WebSocket server.
- `drone_control` (RPi/producer side): camera/telemetry + flight command execution + WebSocket client.

## 1. Establish connection between laptop server and RPi

### Prerequisites

1. Code is available on both machines (laptop and RPi).
2. Docker Engine + Docker Compose plugin are installed on both machines.
3. For real hardware tests on RPi:
   - camera device(s) available (`/dev/video*` or Picamera2 stack),
   - Pixhawk serial device available (default `/dev/ttyAMA0`).
4. VLM API access:
   - OpenAI: `OPEN_AI_KEY`
   - Gemini: `GEMINI_AI_KEY`
5. Optional (for non-LAN connection): `cloudflared` installed on the laptop.
6. For server-side video conversion in `PULL_RECORDINGS`: `ffmpeg` available in the server runtime.

### Step A: prepare environment files

Run on both laptop and RPi:

```bash
cd docker
cp .env_example .env
```

Edit `docker/.env` on the laptop (server side):

```dotenv
MODEL_BACKEND=openai
MODEL_NAME=oai-gpt-4o
OPEN_AI_KEY=...your_key...
# or:
# MODEL_BACKEND=gemini
# MODEL_NAME=gemini-2.5-flash
# GEMINI_AI_KEY=...your_key...

# Recording pull/convert output
RECORDINGS_DIR=/recordings
PULL_BATCH_SIZE=2
PULL_CHUNK_BYTES=524288
```

Edit `docker/.env` on the RPi (producer side):

```dotenv
SERVER_URL=ws://<LAPTOP_IP>:8080
MAV_DEVICE=/dev/ttyAMA0
VIDEO_DEVICE=/dev/video0
RECORD_FPS=30
```

If you use Cloudflare Tunnel, set `SERVER_URL` to `wss://...` (see Step D).

### Step B: build Docker images

Laptop:

```bash
docker build -t flysearch:latest .
```

RPi:

```bash
docker build -t flysearch:latest .
```

### Step C: start mission server on laptop

```bash
cd docker
docker compose --profile server run --rm --service-ports server
```

Keep this terminal open.  
You should see lines like:

- `[WS] Starting server on 0.0.0.0:8080...`
- `[WS] Server is running and listening for connections.`

### Step D (optional): expose laptop server with Cloudflare

In a second laptop terminal:

```bash
cloudflared tunnel --url http://localhost:8080/
```

If Cloudflare prints a URL like `https://xyz.trycloudflare.com`, set on RPi (in .env):

```dotenv
SERVER_URL=wss://xyz.trycloudflare.com
```

### Step E: start producer on RPi

```bash
cd docker
docker compose --profile producer up --build
```

Connection is established when:

- laptop/server logs: `[WS] connected: (...)`
- RPi/producer logs: `[RPi] WS open`

Stop both sides with `Ctrl+C`.

### Test mode without drone hardware (optional)

Run producer test profile instead of `producer`:

```bash
cd docker
docker compose --profile producer_test up --build
```

## 2. Perform a FlySearch-style search

Use the server terminal (the `mission_control` CLI).  
Recommended command:

```text
SEARCH <name> <FS-1|FS-2> object=<target> glimpses=<max_moves> [area=<meters_for_FS-1>] minimum_altitude=<minimum_altitude>
```

Example:

```text
SEARCH test_fs1 FS-1 object=helipad glimpses=6 area=80 minimum_altitude=10
```

The `SEARCH` flow already implements the full FlySearch loop:

1. User specifies prompt:
   - `PromptManager` generates/saves the prompt from `FS-1`/`FS-2` + args.
2. User takes photo and reads telemetry:
   - server requests `PHOTO_WITH_TELEMETRY`,
   - RPi captures photo + telemetry and sends both.
3. User initializes VLM chat and passes metadata:
   - chat is initialized with generated prompt (`CHAT_INIT` internally),
   - each iteration sends image + telemetry context to VLM (`SEND_TO_VLM` internally).
4. User accepts/rejects proposed command:
   - CLI asks:
     - `Enter` / `yes`: accept and send move,
     - `no`: reject and stop,
     - `w`: send collision warning and re-query VLM.
5. Steps 2-4 repeat until:
   - object is found (`FOUND`), or
   - max moves (`glimpses`) is reached, or
   - user cancels.

### Useful manual commands (same CLI)

```text
PROMPT FS-1 object=helipad glimpses=6 area=80 minimum_altitude=10
CHAT_INIT
PHOTO_WITH_TELEMETRY
START_RECORDING
STOP_RECORDING
GET_RECORDINGS
PULL_RECORDINGS video_20260228_120000.h264 video_20260228_121500.h264
SEND_TO_VLM
MOVE
CHAT_SAVE <name>
CHAT_RETRIEVE <name>
CHAT_RESET
```

Notes:

1. `CHAT_INIT` requires a prompt to exist first (`PROMPT ...`).
2. `SEND_TO_VLM` requires cached photo + telemetry (`PHOTO_WITH_TELEMETRY` first).
3. On success, laptop-side artifacts are written to:
   - `uploads/` (images),
   - `telemetry/` (telemetry JSON),
   - `saved_chats/` (chat history),
   - `prompts/` (prompt text + metadata),
   - `recordings/raw/` (pulled `.h264`),
   - `recordings/meta/` (pulled recording metadata),
   - `recordings/mp4/` (converted `.mp4`).

## 3. Retrieve and convert recordings

Use these commands in `mission_control` CLI:

```text
GET_RECORDINGS
PULL_RECORDINGS <name1.h264> [name2.h264 ...]
```

Behavior:

1. `GET_RECORDINGS` asks the RPi for available `.h264` files in `VIDEO_DIR`.
2. `PULL_RECORDINGS` streams selected files from RPi to server in chunks (batched).
3. Server saves pulled raw files and metadata, then converts to `.mp4` using `record_fps` from metadata (fallback: `RECORD_FPS`).
4. Conversion runs on server side, while RPi keeps original `.h264` files.
