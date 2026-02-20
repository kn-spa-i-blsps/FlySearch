from datetime import datetime
import uuid


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def build_session_id() -> str:
    shortid = uuid.uuid4().hex[:8]
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{shortid}"
