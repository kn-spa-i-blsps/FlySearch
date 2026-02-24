import json
import re
from dataclasses import dataclass
from typing import Dict, Tuple

from mission_control.core.exceptions import ParsingError


def parse_telemetry(path):
    """ Parses telemetry data from JSON file.

        Returns message for the VLM about current height.
    """

    with open(path, "r", encoding="utf-8") as f:
        telemetry = json.load(f)

    telemetry_data = telemetry.get("data", {})
    height = telemetry_data.get("position", {}).get("alt")
    if height is None:
        height = 10
    return [f"Your current altitude is {height} meters above ground level.", height]

def parse_prompt_arguments(cmd):
    """Divides arguments for the prompt command.

        Returns kind of prompt and dictionary of arguments.
    """

    parts = cmd.split()
    if len(parts) < 1:
        print("Usage: PROMPT FS-1|FS-2 [object=.. glimpses=.. area=..]")
        raise ValueError
    kind = parts[0].upper()
    if kind not in ("FS-1", "FS-2"):
        print("Kind must be FS-1 or FS-2")
        raise ValueError

    kv: Dict[str, int | str] = {}
    for token in parts[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    _coerce_positive_int(kv, "glimpses")
    _coerce_positive_int(kv, "area")
    return kind, kv

def parse_search_arguments(cmd):
    """Divides arguments for the search command.

        Returns name, kind of prompt and dictionary of arguments.
    """

    parts = cmd.split()
    if len(parts) not in [4, 5]:
        print("Usage: SEARCH <NAME> <FS-1|FS-2> [object=.. glimpses=.. area=..]")
        raise ValueError
    name = parts[0].upper()
    kind = parts[1].upper()
    if kind not in ("FS-1", "FS-2"):
        print("Kind must be FS-1 or FS-2")
        raise ValueError

    kv: Dict[str, int | str] = {}
    for token in parts[2:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    if "glimpses" not in kv:
        print("SEARCH requires glimpses=<max_moves>")
        raise ValueError

    _coerce_positive_int(kv, "glimpses")
    _coerce_positive_int(kv, "area")
    return name, kind, kv


def _coerce_positive_int(kv: Dict[str, int | str], key: str) -> None:
    """Convert numeric CLI options to positive integers in place."""
    if key not in kv:
        return

    try:
        value = int(str(kv[key]).strip())
    except (TypeError, ValueError):
        print(f"{key} must be an integer.")
        raise ValueError

    if value <= 0:
        print(f"{key} must be greater than 0.")
        raise ValueError

    kv[key] = value


@dataclass
class ModelResponse:
    found: bool = False
    move: Tuple[float, float, float] = None

def parse_xml_response(model_response: str) -> ModelResponse:
    xml_response_pattern = re.compile(r"^.*?<action>(.*?)</action>.*$", flags=re.DOTALL)
    model_response = model_response.lower().strip()
    match = xml_response_pattern.match(model_response)

    if not match:
        if "found" in model_response:
            # If the response contains 'found' but doesn't match the XML pattern, assume it's a found action
            return ModelResponse(found=True)

        raise ParsingError(f"Invalid XML response: {model_response}")

    action = match.group(1).strip()

    if "found" in action:
        return ModelResponse(found=True)

    try:
        action = action.replace("(", "").replace(")", "")
        action = action.split(",")
        # east_diff, north_diff, up_diff
        action = float(action[0]), float(action[1]), float(action[2])
    except (ValueError, IndexError):
        raise ParsingError(f"Invalid action format: {action}")

    return ModelResponse(move=action)
