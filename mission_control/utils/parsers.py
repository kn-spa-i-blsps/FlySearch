import json
import re
from dataclasses import dataclass
from typing import Dict, Tuple

import aiofiles

from mission_control.core.exceptions import ParsingError
from mission_control.utils.logger import get_configured_logger

logger = get_configured_logger(__name__)


async def get_height_async(path):
    """ Parses telemetry data from JSON file asynchronously.

        Returns height in meters.
    """

    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        content = await f.read()
        telemetry = json.loads(content)

    telemetry_data = telemetry.get("data", {})
    height = telemetry_data.get("position", {}).get("alt")

    if height is None:
        height = 10

    return height


def parse_prompt_arguments(cmd):
    """Divides arguments for the prompt command.

        Returns kind of prompt and dictionary of arguments.
    """

    parts = cmd.split()
    if len(parts) < 1:
        logger.info("Usage: PROMPT FS-1|FS-2 [object=.. glimpses=.. area=.. minimum_altitude=..]")
        raise ValueError
    kind = parts[0].upper()
    if kind not in ("FS-1", "FS-2"):
        logger.info("Kind must be FS-1 or FS-2")
        raise ValueError

    kv: Dict[str, int | str] = {}
    for token in parts[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    _coerce_positive_int(kv, "glimpses")
    _coerce_positive_int(kv, "area")
    _coerce_positive_int(kv, "minimum_altitude")
    return kind, kv


def parse_search_arguments(cmd):
    """Divides arguments for the search command.

        Returns name, kind of prompt and dictionary of arguments.
    """

    parts = cmd.split()
    if len(parts) not in [6, 7]:
        logger.info(
            "Usage: SEARCH <mission_id> <drone_id> <FS-1|FS-2> [object=.. glimpses=.. area=.. minimum_altitude=..]")
        raise ValueError
    mission_id = parts[0]
    drone_id = parts[1]
    kind = parts[2].upper()
    if kind not in ("FS-1"
                    "", "FS-2"):
        logger.info("Kind must be FS-1 or FS-2")
        raise ValueError

    kv: Dict[str, int | str] = {}
    for token in parts[3:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    if "glimpses" not in kv:
        logger.info("SEARCH requires glimpses=<max_moves>")
        raise ValueError

    _coerce_positive_int(kv, "glimpses")
    _coerce_positive_int(kv, "area")
    _coerce_positive_int(kv, "minimum_altitude")
    return mission_id, drone_id, kind, kv


def _coerce_positive_int(kv: Dict[str, int | str], key: str) -> None:
    """Convert numeric CLI options to positive integers in place."""
    if key not in kv:
        return

    try:
        value = int(str(kv[key]).strip())
    except (TypeError, ValueError):
        logger.warning(f"{key} must be an integer.")
        raise ValueError

    if value <= 0:
        logger.warning(f"{key} must be greater than 0.")
        raise ValueError

    kv[key] = value


@dataclass
class ModelResponse:
    found: bool = False
    move: Tuple[float, float, float] = (0, 0, 0)
    reasoning: str = ""


ACTION_PATTERN = re.compile(r"<action>(.*?)</action>", flags=re.DOTALL | re.IGNORECASE)
REASONING_PATTERN = re.compile(r"<reasoning>(.*?)</reasoning>", flags=re.DOTALL | re.IGNORECASE)


def parse_xml_response(model_response: str) -> ModelResponse:
    model_response_clean = model_response.strip()
    model_response_lower = model_response_clean.lower()

    reasoning = ""
    reasoning_match = REASONING_PATTERN.search(model_response_clean)
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()

    action_match = ACTION_PATTERN.search(model_response_clean)

    if not action_match:
        if "found" in model_response_lower:
            return ModelResponse(found=True, reasoning=reasoning)

        raise ParsingError(f"Invalid XML response: {model_response_clean}")

    action = action_match.group(1).lower().strip()

    if "found" in action:
        return ModelResponse(found=True, reasoning=reasoning)

    try:
        action_clean = action.replace("(", "").replace(")", "")
        action_parts = action_clean.split(",")
        # east_diff, north_diff, up_diff
        move = (float(action_parts[0]), float(action_parts[1]), float(action_parts[2]))
    except (ValueError, IndexError):
        raise ParsingError(f"Invalid action format: {action}")

    return ModelResponse(move=move, reasoning=reasoning)
