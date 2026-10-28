"""Sanity checks on model commands before they reach the match server.

The model sees a text summary and can still ask for things the server cannot
do. In the first matches the goalkeeper sent GK_DISTRIBUTE on 63 of 64 ticks
without holding the ball, which the server ignores, so the keeper spent the
match standing still. These checks catch that class of mistake and let the
rule-based fallback decide instead.
"""

from state import get_possession_info, holder_is_me, pitch_bounds, pitch_scale
from tactics import in_shooting_range, pass_options, pressure

POSSESSION_COMMANDS = {"PASS", "SHOOT", "GK_DISTRIBUTE"}
"""Commands that only do something when this player holds the ball."""

OUT_OF_POSSESSION_COMMANDS = {"PRESS_BALL", "INTERCEPT"}
"""Commands that chase the ball, pointless while our own team holds it.

In the second match the defender sent PRESS_BALL 26 times while a teammate
had the ball, running at its own player and leaving the back line empty.
"""


def needs_fallback(commands: list[dict], game_state: dict, team_id: int, my_player_id: int):
    """Reason string when the commands cannot work this tick, else None."""
    ball = game_state.get("ball", {})
    players = game_state.get("players", [])
    for cmd in commands:
        ctype = cmd.get("commandType")
        if ctype in POSSESSION_COMMANDS and not holder_is_me(ball, players, team_id, my_player_id):
            return f"{ctype} without the ball"
        if ctype in OUT_OF_POSSESSION_COMMANDS and get_possession_info(ball, players, team_id)[2]:
            return f"{ctype} while our team has the ball"
    return None


def tactical_override(commands: list[dict], game_state: dict, team_id: int, my_player_id: int,
                      position_label: str):
    """Reason string when a possible command breaks a team-play rule, else None.

    Rules for the player holding the ball (tactics.on_ball_decision replaces
    the command):
      - the goalkeeper always clears long;
      - no back pass inside our defensive third;
      - under pressure, carry the ball into space unless the model chose an
        open, unblocked pass or a shot in range.
    """
    ball = game_state.get("ball", {})
    players = game_state.get("players", [])
    if not holder_is_me(ball, players, team_id, my_player_id):
        return None
    if position_label == "GK":
        cmd = commands[0] if commands else {}
        params = cmd.get("parameters") or {}
        if cmd.get("commandType") != "GK_DISTRIBUTE" or params.get("method") != "KICK":
            return "goalkeeper clears long"
        return None

    options = {o["id"]: o for o in pass_options(game_state, team_id, my_player_id)}
    under, _ = pressure(game_state, team_id, my_player_id)
    in_range = in_shooting_range(game_state, team_id, my_player_id)

    for cmd in commands:
        ctype = cmd.get("commandType")
        if ctype == "PASS":
            option = options.get((cmd.get("parameters") or {}).get("target_player_id"))
            if option and option["forbidden"]:
                return "back pass in our defensive third"
            if under and not (option and option["open"] and not option["blocked"]):
                return "under pressure without an open pass, carry the ball"
        elif under and not (ctype == "SHOOT" and in_range):
            return "under pressure, carry the ball"
    return None


def fit_to_pitch(commands: list[dict], game_state: dict) -> list[str]:
    """Keep MOVE_TO targets on the pitch, in place. Returns notes on changes.

    A target far outside a small pitch almost always means the model reasoned
    in reference-pitch units (x ±55), so it is scaled down first; anything
    still outside is clamped to the touchline.
    """
    scale = pitch_scale(game_state)
    half_length, half_width = pitch_bounds(scale)
    notes = []
    for cmd in commands:
        if cmd.get("commandType") != "MOVE_TO":
            continue
        params = cmd.get("parameters") or {}
        x, y = params.get("target_x"), params.get("target_y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        nx, ny = float(x), float(y)
        if scale < 1.0 and (abs(nx) > 1.5 * half_length or abs(ny) > 1.5 * half_width):
            nx, ny = nx * scale, ny * scale
        nx = max(-half_length, min(half_length, nx))
        ny = max(-half_width, min(half_width, ny))
        if (nx, ny) != (x, y):
            params["target_x"], params["target_y"] = round(nx, 2), round(ny, 2)
            notes.append(f"MOVE_TO ({x}, {y}) -> ({params['target_x']}, {params['target_y']})")
    return notes
