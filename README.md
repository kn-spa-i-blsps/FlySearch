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
```

Edit `docker/.env` on the RPi (producer side):

```dotenv
SERVER_URL=ws://<LAPTOP_IP>:8080
MAV_DEVICE=/dev/ttyAMA0
VIDEO_DEVICE=/dev/video0
```

If you use Cloudflare Tunnel, set `SERVER_URL` to `wss://...` (see Step D).

### Step B: build Docker images

Laptop:

```bash
cd docker
docker compose --profile server build server
```

RPi:

```bash
cd docker
docker compose --profile producer build producer
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

If Cloudflare prints a URL like `https://xyz.trycloudflare.com`, set on RPi:

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
SEARCH <name> <FS-1|FS-2> object=<target> glimpses=<max_moves> [area=<meters_for_FS-1>]
```

Example:

```text
SEARCH test_fs1 FS-1 object=helipad glimpses=6 area=80
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
PROMPT FS-1 object=helipad glimpses=6 area=80
CHAT_INIT
PHOTO_WITH_TELEMETRY
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
   - `prompts/` (prompt text + metadata).
