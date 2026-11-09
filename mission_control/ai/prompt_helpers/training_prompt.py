"""
Prompt, tool definitions and observation format used to train FlyLoRA.

Everything here is copied verbatim from https://github.com/apardyl/rl-uav-search
(scripts/train_episode_tools.py, src/environment/gsplat/gsplat_env.py,
src/environment/coordinates.py, src/environment/movement.py) so that every model
sees the same conversation that FlyLoRA was trained on. Do not reword anything here
without checking the training repository first.
"""

SUCCESS_THRESHOLD_M = 10.0

# First text part of the first user message (the dataset prompt row in training).
# The initial observation (image + mission details) is appended to the same message.
INITIAL_USER_TEXT = "Below you will see the initial camera image and mission details."

MOVE_NAVIGATION_HINT = (
    "Use move(dx, dy, dz) to navigate "
    "(right/forward/up relative to the camera view, in meters). "
    "Use found() when directly above the target at low altitude."
)

# Tool schemas exactly as TRL generated them from the GsplatUAVEnv method docstrings
# (transformers.utils.get_json_schema, transformers==5.12.1). TRL lists environment
# methods via inspect.getmembers, i.e. alphabetically: found, then move.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "found",
            "description": (
                "Declare that the target has been found.\n\n"
                "Succeeds only if the target is visible in the downward camera AND the UAV\n"
                "altitude low enough above the target.  On failure\n"
                "the episode ends immediately with ``reward = 0``."
            ),
            "parameters": {"type": "object", "properties": {}},
            "return": {
                "type": "string",
                "description": (
                    "A short string indicating the episode has ended and no further action "
                    "is needed. There is no feedback on success or failure available."
                ),
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": "Move the UAV relative to its current position and observe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dx": {
                        "type": "number",
                        "description": "Right displacement in meters (positive = right in camera view).",
                    },
                    "dy": {
                        "type": "number",
                        "description": "Forward displacement in meters (positive = toward top of image).",
                    },
                    "dz": {
                        "type": "number",
                        "description": (
                            "Vertical displacement in meters (positive = up, negative = down). "
                            "Pass ``(0, 0, 0)`` to observe the current position without moving."
                        ),
                    },
                },
                "required": ["dx", "dy", "dz"],
            },
            "return": {
                "type": "object",
                "description": (
                    "Multimodal content list:\n"
                    '    ``[{"type": "image", "image": PIL.Image}, {"type": "text", "text": str}]``'
                ),
            },
        },
    },
]


def build_system_prompt(success_threshold: float = SUCCESS_THRESHOLD_M) -> str:
    return f"""
You control a UAV drone searching for a target object from above.

The downward-facing camera shows what is directly beneath you.  The first\
 message includes the initial camera image and mission details from the\
 environment. After each call you will receive a new camera image. Use the tools to navigate and locate the target:

- move(dx, dy, dz): Move relative to your current position. All values in metres.
- found: Declare you have located the target.
    Only call this when the center of the target object is clearly visible in the camera AND you are\
 close to the ground above it (at most {success_threshold}m above the target). This will finish the episode.

Search efficiently. You have a limited number of steps. If there are multiple objects matching the target description,
you should find any one of them. Do not fly outside of the map. You may see visual artefacts in the camera image or map edges - this is normal and expected.
Some of the images may come from a simulator and may not be realistic, with low polygon count and simple textures. Other images may come from a real camera, be noisy, and have artifacts.

**Strategy Tips:**
- Start with a systematic search pattern to cover the area,
- Adjust altitude for better visibility (higher = wider view, lower = more detail),
- When you spot the target, descend to at most {success_threshold}m altitude before declaring found().

**Image Grid Overlay:**
There is a grid overlaid on each image you are presented with. It is meant to (roughly) communicate which point will be in the drone's center of vision if you move in that direction. Each grid point shows coordinates relative to your current position in meters (X, Y) where X is right/left and Y is forward/back in the camera view. E.g., if you move (10, 0, 0), the grid point (10, 0) will be in the center of your vision.

**Thinking:**
Please keep your thinking responses concise and to the point, as there is a limited number of tokens available for the model to use.
"""


def format_move_feedback(success: bool, reason: str, position_changed: bool) -> str:
    if success:
        return "Movement successful."
    if position_changed:
        return f"Movement limited: {reason}"
    return f"Movement failed: {reason}"


def altitude_limit_reason(min_altitude: float, max_altitude: float) -> str:
    return f"Altitude limit ({min_altitude:.1f}m – {max_altitude:.1f}m)"


def format_bounds(half_area: float, min_altitude: float, max_altitude: float) -> str:
    return (
        f"Map bounds: X [{-half_area:.1f}, {half_area:.1f}] "
        f"Y [{-half_area:.1f}, {half_area:.1f}] "
        f"Alt [{min_altitude:.1f}, {max_altitude:.1f}] m"
    )


def format_position(x: float, y: float, altitude: float) -> str:
    return f"({x:.1f}, {y:.1f}, {altitude:.1f})"


def format_initial_observation(
    task_hint: str, position: str, bounds: str, max_steps: int
) -> str:
    return (
        "\n"
        f"Task: {task_hint}\n"
        f"Current position: {position}\n"
        f"{bounds}\n"
        f"Max steps: {max_steps}\n"
        f"{MOVE_NAVIGATION_HINT}"
    )


def format_step_observation(
    feedback: str, position: str, step: int, max_steps: int, bounds: str
) -> str:
    return (
        f"{feedback}\n"
        f"Position: {position}\n"
        f"Step: {step}/{max_steps}\n"
        f"{bounds}"
    )
