# flysearch

Procedure to run `mission_control` (laptop/server) and `drone_control` (RPi/producer)

1. Make sure Docker is running.
2. Create `docker/.env` from `docker/.env_example` on both laptop and RPi.
3. In `docker/.env` on laptop, set:
   - `MODEL_BACKEND` and `MODEL_NAME`
   - matching API key (`OPEN_AI_KEY` for OpenAI or `GEMINI_AI_KEY` for Gemini)
4. On laptop, start server (`mission_control`):
```bash
cd docker
docker compose --profile server up --build
```
5. Optional remote access: in a new laptop terminal, run:
```bash
cloudflared tunnel --url http://localhost:8080/
```
6. If using Cloudflare URL, convert `https://...` to `wss://...` and set that value as `SERVER_URL` on RPi.
7. On RPi, set `SERVER_URL` in `docker/.env` to your laptop endpoint.
8. On RPi, start producer (`drone_control`):
```bash
cd docker
docker compose --profile producer up --build
```
9. Received photos are stored on laptop in `uploads/`.
10. Stop gracefully with `Ctrl+C` in compose terminals.

Test mode without drone hardware:
```bash
cd docker
docker compose --profile producer_test up --build
```
