import json
from typing import Dict


def parse_telemetry(path):
    """ Parses telemetry data from JSON file.

        Returns message for the VLM about current height.
    """

    with open(path, "r", encoding="utf-8") as f:
        telemetry = json.load(f)

    telemetry_data = telemetry.get("data", {})
    height = telemetry_data.get("position", {}).get("alt", 10)
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

    kv: Dict[str, str] = {}
    for token in parts[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    return kind, kv

def parse_search_arguments(cmd):
    """Divides arguments for the search command.

        Returns name, kind of prompt and dictionary of arguments.
    """

    parts = cmd.split()
    if len(parts) < 1:
        print("Usage: SEARCH <NAME> <FS-1|FS-2> [object=.. glimpses=.. area=..]")
        raise ValueError
    name = parts[0].upper()
    kind = parts[1].upper()
    if kind not in ("FS-1", "FS-2"):
        print("Kind must be FS-1 or FS-2")
        raise ValueError

    kv: Dict[str, str] = {}
    for token in parts[2:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    return name, kind, kv