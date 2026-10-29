"""Sanity checks on model commands before they reach the match server.

The model sees a text summary and can still ask for things the server cannot
do. In the first matches the goalkeeper sent GK_DISTRIBUTE on 63 of 64 ticks
without holding the ball, which the server ignores, so the keeper spent the
match standing still. These checks catch that class of mistake and let the
rule-based fallback decide instead.
"""

import math

from state import (
    _is_my_team,
    _player_idx,
    get_goal_positions,
    get_possession_info,
    holder_is_me,
    pitch_bounds,
    pitch_scale,
)
from tactics import (
    ball_near_our_goal,
    first_defender,
    goes_for_ball,
    in_shooting_range,
    keeper_leaves_zone,
    pass_options,
    pressure,
    runs_backward,
)

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
        if position_label != "GK" and first_defender(game_state, team_id) == my_player_id:
            if not goes_for_ball(commands, game_state):
                return "first defender goes for the ball"
            return None
        if position_label == "GK":
            if any(keeper_leaves_zone(c, game_state, team_id) for c in commands):
                return "goalkeeper stays home"
        elif position_label in ("MID", "FWD1", "FWD2") and get_possession_info(ball, players, team_id)[2]:
            if any(runs_backward(c, game_state, team_id, my_player_id) for c in commands):
                return "backward run while attacking"
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
        elif runs_backward(cmd, game_state, team_id, my_player_id):
            return "carrying the ball backward"
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


TIRED = 0.25
"""Stamina (0-1) below which a player stops sprinting. In P8 FWD2 spent half
the match under 0.2, from second 14 on, mostly pressing at full speed."""
CALM_PRESS = 0.45
"""PRESS_BALL intensity that still tackles (above 0.3) without sprinting
(above 0.5), per the workshop's command reference."""


def pace(commands: list[dict], game_state: dict, team_id: int, my_player_id: int) -> list[str]:
    """Stop a tired player sprinting, in place. Returns notes on changes.

    Sprinting drains stamina and our players ran out early. A tired player
    walks to MOVE_TO targets and presses at CALM_PRESS, except when the ball
    is near our goal, where every step counts.
    """
    me = next((p for p in game_state.get("players", [])
               if _is_my_team(p, team_id) and _player_idx(p) == my_player_id), None)
    stamina = (me or {}).get("stamina", 1.0)
    if not isinstance(stamina, (int, float)) or stamina >= TIRED or ball_near_our_goal(game_state, team_id):
        return []
    notes = []
    for cmd in commands:
        params = cmd.get("parameters") or {}
        if cmd.get("commandType") == "MOVE_TO" and params.get("sprint"):
            params["sprint"] = False
            notes.append(f"stamina {stamina:.2f}: MOVE_TO without sprint")
        elif cmd.get("commandType") == "PRESS_BALL" and (params.get("intensity") or 0) > CALM_PRESS:
            notes.append(f"stamina {stamina:.2f}: PRESS_BALL {params.get('intensity')} -> {CALM_PRESS}")
            params["intensity"] = CALM_PRESS
    return notes


def fix_mark_target(commands: list[dict], game_state: dict, team_id: int) -> list[str]:
    """Point MARK at an opponent outfield player, in place.

    In P8 and P9 the defender marked the opposing keeper (id 0) six times and
    a player 5 that does not exist once. Those become a mark on the opponent
    closest to our goal, which is what lesson L1 asks for.
    """
    opponents = [p for p in game_state.get("players", [])
                 if not _is_my_team(p, team_id) and _player_idx(p) not in (None, 0)]
    if not opponents:
        return []
    valid = {_player_idx(p) for p in opponents}
    my_goal_x, _ = get_goal_positions(team_id, pitch_scale(game_state))
    notes = []
    for cmd in commands:
        params = cmd.get("parameters") or {}
        target = params.get("target_player_id")
        if cmd.get("commandType") != "MARK" or target in valid:
            continue
        closest = min(opponents, key=lambda p: math.dist(
            (p.get("position", {}).get("x", 0), p.get("position", {}).get("y", 0)), (my_goal_x, 0)))
        params["target_player_id"] = _player_idx(closest)
        notes.append(f"MARK {target} -> {params['target_player_id']} (closest opponent to our goal)")
    return notes
