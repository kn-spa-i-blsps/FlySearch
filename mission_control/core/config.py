import os
from pathlib import Path


class Config:
    """ Configuration variables - dirs, ports, hosts... """

    def __init__(self):
        # VLM model for the LLM backend factories.
        self.model_backend = os.environ.get("MODEL_BACKEND", "gemini")
        self.model_name = os.environ.get("MODEL_NAME", "gemini-2.5-flash")

        # Host and port on which to listen for the data from the drone.
        self.host = os.environ.get("WS_HOST", "0.0.0.0")
        self.port = int(os.environ.get("WS_PORT", "8080"))

        # Maximum size of the message.
        self.max_ws_mb = int(os.environ.get("MAX_WS_MB", "25"))

        # Directories to save the output.
        self.chats_dir = Path(os.environ.get("CHATS_DIR", "saved_chats"))
        self.upload_dir = os.environ.get("UPLOAD_DIR", "uploads")
        self.prompts_dir = os.environ.get("PROMPTS_DIR", "prompts")
        self.telemetry_dir = os.environ.get("TELEMETRY_DIR", "telemetry")
        self.recordings_dir = Path(os.environ.get("RECORDINGS_DIR", "recordings"))
        self.recordings_raw_dir = self.recordings_dir / "raw"
        self.recordings_mp4_dir = self.recordings_dir / "mp4"
        self.recordings_meta_dir = self.recordings_dir / "meta"
        os.makedirs(self.chats_dir, exist_ok=True)
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.prompts_dir, exist_ok=True)
        os.makedirs(self.telemetry_dir, exist_ok=True)
        os.makedirs(self.recordings_raw_dir, exist_ok=True)
        os.makedirs(self.recordings_mp4_dir, exist_ok=True)
        os.makedirs(self.recordings_meta_dir, exist_ok=True)

        # Pull/transfer defaults.
        self.pull_batch_size = int(os.environ.get("PULL_BATCH_SIZE", "2"))
        self.pull_chunk_bytes = int(os.environ.get("PULL_CHUNK_BYTES", str(512 * 1024)))
        self.record_fps_default = int(os.environ.get("RECORD_FPS", "30"))

        # here just to remember to add it to environ.
        self.gemini_api_key = os.environ.get("GEMINI_AI_KEY", None)
        self.gpt_api_key = os.environ.get("OPEN_AI_KEY", None)
