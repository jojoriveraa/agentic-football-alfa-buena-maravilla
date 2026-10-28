"""Team-play tactics: passing triangles, safe passes, carrying under pressure,
long goalkeeper clearances.

The same calculations feed three places, so they never disagree:
  - team_play_lines(): hints appended to the state the model reads
  - tactical_override() in guards.py: hard rules the model may not break
  - on_ball_decision() / support_move(): what the code plays instead

Small models are unreliable at geometry ("is this pass backwards?", "which
way is open?"), so the geometry is computed here and the model gets the
answer. All distances are reference-pitch units (x ±55, y ±35) scaled by
pitch_scale(), like the fallback configs.
"""

import math

from fallback import _cmd
from state import (
    _is_my_team,
    _player_idx,
    dist,
    get_goal_positions,
    get_possession_info,
    holder_is_me,
    pitch_bounds,
    pitch_scale,
    SHOOT_RANGE,
)

PRESSURE_RADIUS = 10.0
"""An opponent this close to the ball carrier means it is under pressure."""
OPEN_RADIUS = 8.0
"""A receiver with no opponent this close is open."""
LANE_CLEARANCE = 4.0
"""An opponent this close to the passing line blocks the pass."""
OWN_THIRD_DEPTH = 110.0 / 3
"""Depth of our defensive third, where back passes are forbidden."""
SUPPORT_AHEAD = 14.0
SUPPORT_WIDE = 10.0
DRIBBLE_STEP = 12.0
EDGE_MARGIN = 3.0
DRIBBLE_ANGLES = (0.0, 35.0, -35.0, 60.0, -60.0)

TEAM_PLAY_RULES = """## Team Play (hints are computed for the real pitch; these rules win over your role rules)
- When YOUR PLAYER has the ball, a "WITH THE BALL" block lists your options:
  1. distOppGoal <= shootRange → SHOOT.
  2. underPressure=True and no option marked open → carry the ball into space: MOVE_TO dribbleTo with sprint=true.
  3. Otherwise PASS to bestPass with the pass type shown.
  4. Never pass to an option marked FORBIDDEN (back pass near your own goal).
- When a teammate has the ball, the state shows your supportSpot: MOVE_TO it to form a passing triangle with the ball carrier."""


def attack_dir(team_id: int) -> int:
    """+1 when attacking toward +x (HOME), -1 toward -x (AWAY)."""
    return 1 if team_id == 0 else -1


def _xy(p: dict) -> tuple[float, float]:
    pos = p.get("position", {})
    return float(pos.get("x", 0)), float(pos.get("y", 0))


def _split(game_state: dict, team_id: int, my_player_id: int):
    players = game_state.get("players", [])
    mine = [p for p in players if _is_my_team(p, team_id)]
    opps = [p for p in players if not _is_my_team(p, team_id)]
    me = next((p for p in mine if _player_idx(p) == my_player_id), None)
    return me, [p for p in mine if p is not me], opps


def _clamp(x: float, y: float, scale: float) -> tuple[float, float]:
    half_length, half_width = pitch_bounds(scale)
    m = EDGE_MARGIN * scale
    return (
        round(max(-half_length + m, min(half_length - m, x)), 2),
        round(max(-half_width + m, min(half_width - m, y)), 2),
    )


def _segment_distance(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def in_own_third(pos, team_id: int, scale: float) -> bool:
    my_goal_x, _ = get_goal_positions(team_id, scale)
    return abs(pos[0] - my_goal_x) < OWN_THIRD_DEPTH * scale


def pressure(game_state: dict, team_id: int, my_player_id: int):
    """(under_pressure, distance to nearest opponent) for this player."""
    scale = pitch_scale(game_state)
    me, _, opps = _split(game_state, team_id, my_player_id)
    if not me or not opps:
        return False, None
    d = min(math.dist(_xy(me), _xy(o)) for o in opps)
    return d < PRESSURE_RADIUS * scale, d


def in_shooting_range(game_state: dict, team_id: int, my_player_id: int) -> bool:
    scale = pitch_scale(game_state)
    me, _, _ = _split(game_state, team_id, my_player_id)
    _, opp_goal_x = get_goal_positions(team_id, scale)
    return bool(me) and abs(_xy(me)[0] - opp_goal_x) <= SHOOT_RANGE * scale


def pass_options(game_state: dict, team_id: int, my_player_id: int) -> list[dict]:
    """Every outfield teammate as a pass option, with direction and safety."""
    scale = pitch_scale(game_state)
    me, mates, opps = _split(game_state, team_id, my_player_id)
    if not me:
        return []
    a = attack_dir(team_id)
    mx, my = _xy(me)
    no_back = in_own_third((mx, my), team_id, scale)
    half_length, _ = pitch_bounds(scale)
    options = []
    for t in mates:
        pid = _player_idx(t)
        tx, ty = _xy(t)
        progress = a * (tx - mx)
        # The keeper is listed so a pass to it is caught as a back pass, the
        # most dangerous one near our goal, but best_pass() never picks it.
        back = pid == 0 or progress < -0.05 * half_length
        nearest_opp = min((math.dist((tx, ty), _xy(o)) for o in opps), default=99.0)
        blocked = any(
            _segment_distance(_xy(o), (mx, my), (tx, ty)) < LANE_CLEARANCE * scale for o in opps
        )
        options.append({
            "id": pid,
            "progress": progress,
            "dist": math.dist((mx, my), (tx, ty)),
            "open": nearest_opp > OPEN_RADIUS * scale,
            "blocked": blocked,
            "back": back,
            "forbidden": back and no_back,
            "keeper": pid == 0,
        })
    return options


def best_pass(options: list[dict]):
    """Most forward safe option: open and unblocked first, then just unblocked."""
    allowed = [o for o in options if not (o["forbidden"] or o["blocked"] or o["keeper"])]
    for pool in ([o for o in allowed if o["open"]], allowed):
        if pool:
            return max(pool, key=lambda o: o["progress"] - 0.25 * o["dist"])
    return None


def pass_type(option: dict, scale: float) -> str:
    """THROUGH into space for longer forward balls, GROUND otherwise."""
    if option["progress"] > 0 and option["dist"] > SUPPORT_AHEAD * scale * 0.8:
        return "THROUGH"
    return "GROUND"


def dribble_target(game_state: dict, team_id: int, my_player_id: int):
    """The 'self-pass': a point ahead, away from opponents, to carry the ball into.

    The command set has no self-pass, so this is a sprinting MOVE_TO while in
    possession. Candidates fan out from the attacking direction; the one whose
    end point is farthest from every opponent wins, ties going to the most
    direct line.
    """
    scale = pitch_scale(game_state)
    me, _, opps = _split(game_state, team_id, my_player_id)
    if not me:
        return None
    a = attack_dir(team_id)
    mx, my = _xy(me)
    step = DRIBBLE_STEP * scale
    best, best_score = None, -1.0
    for deg in DRIBBLE_ANGLES:
        rad = math.radians(deg)
        x, y = _clamp(mx + a * step * math.cos(rad), my + step * math.sin(rad), scale)
        space = min((math.dist((x, y), _xy(o)) for o in opps), default=99.0)
        score = space - 0.001 * abs(deg)
        if score > best_score:
            best, best_score = (x, y), score
    return best


def gk_clearance_target(game_state: dict, team_id: int):
    """Most advanced outfield teammate, preferring an open one of the two highest."""
    scale = pitch_scale(game_state)
    _, mates, opps = _split(game_state, team_id, 0)
    a = attack_dir(team_id)
    outfield = sorted(
        (t for t in mates if _player_idx(t) != 0), key=lambda t: a * _xy(t)[0], reverse=True
    )
    if not outfield:
        return None
    for t in outfield[:2]:
        if min((math.dist(_xy(t), _xy(o)) for o in opps), default=99.0) > OPEN_RADIUS * scale:
            return _player_idx(t)
    return _player_idx(outfield[0])


def support_spot(game_state: dict, team_id: int, my_player_id: int, position_label: str):
    """Where to stand to form a passing triangle with our ball carrier, or None.

    Fixed shape around the carrier: FWD1 ahead-left, FWD2 ahead-right, MID
    behind on the side away from the ball, DEF as the safety behind the play
    and never past the halfway line. Left/right are from the attacking side.
    """
    if position_label == "GK":
        return None
    ball = game_state.get("ball", {})
    players = game_state.get("players", [])
    _, _, we_hold = get_possession_info(ball, players, team_id)
    if not we_hold or holder_is_me(ball, players, team_id, my_player_id):
        return None
    holder_idx = get_possession_info(ball, players, team_id)[0]
    carrier = next(
        (p for p in players if _is_my_team(p, team_id) and _player_idx(p) == holder_idx), None
    )
    if not carrier:
        return None
    scale = pitch_scale(game_state)
    a = attack_dir(team_id)
    bx, by = _xy(carrier)
    ahead, wide = SUPPORT_AHEAD * scale, SUPPORT_WIDE * scale
    if position_label == "FWD1":
        x, y = bx + a * ahead, by + a * wide
    elif position_label == "FWD2":
        x, y = bx + a * ahead, by - a * wide
    elif position_label == "MID":
        side = -1 if by > 0 else 1
        x, y = bx - a * 0.6 * ahead, by + side * wide
    else:  # DEF: safety, behind the ball and in our own half
        x, y = bx - a * 1.5 * ahead, by * 0.3
        x = min(x, 0.0) if a > 0 else max(x, 0.0)
    return _clamp(x, y, scale)


def support_move(game_state: dict, team_id: int, my_player_id: int, position_label: str):
    spot = support_spot(game_state, team_id, my_player_id, position_label)
    if not spot:
        return None
    return _cmd("MOVE_TO", my_player_id, team_id,
                {"target_x": spot[0], "target_y": spot[1], "sprint": True})


def on_ball_decision(game_state: dict, team_id: int, my_player_id: int, position_label: str) -> dict:
    """What a player with the ball should do, by the team-play rules."""
    scale = pitch_scale(game_state)
    if position_label == "GK":
        target = gk_clearance_target(game_state, team_id)
        return _cmd("GK_DISTRIBUTE", my_player_id, team_id,
                    {"target_player_id": target if target is not None else 1, "method": "KICK"})
    if in_shooting_range(game_state, team_id, my_player_id):
        return _cmd("SHOOT", my_player_id, team_id, {"aim_location": "CENTER", "power": 0.9})
    options = pass_options(game_state, team_id, my_player_id)
    under, _ = pressure(game_state, team_id, my_player_id)
    choice = best_pass(options)
    open_choice = choice if choice and choice["open"] else None
    if (under and not open_choice) or not choice:
        target = dribble_target(game_state, team_id, my_player_id)
        if target:
            return _cmd("MOVE_TO", my_player_id, team_id,
                        {"target_x": target[0], "target_y": target[1], "sprint": True})
    return _cmd("PASS", my_player_id, team_id,
                {"target_player_id": choice["id"], "type": pass_type(choice, scale)})


def team_play_lines(game_state: dict, team_id: int, my_player_id: int, position_label: str) -> list[str]:
    """Hints for the model: options with the ball, or the triangle spot without it."""
    ball = game_state.get("ball", {})
    players = game_state.get("players", [])
    scale = pitch_scale(game_state)
    if holder_is_me(ball, players, team_id, my_player_id):
        if position_label == "GK":
            target = gk_clearance_target(game_state, team_id)
            return [f"WITH THE BALL: clear it long: GK_DISTRIBUTE method KICK to player {target}."]
        under, d = pressure(game_state, team_id, my_player_id)
        options = pass_options(game_state, team_id, my_player_id)
        choice = best_pass(options)
        target = dribble_target(game_state, team_id, my_player_id)
        lines = [f"WITH THE BALL: underPressure={under} nearestOpponent={d:.1f}" if d is not None
                 else "WITH THE BALL: underPressure=False"]
        for o in options:
            tags = ["BACK" if o["back"] else "FORWARD" if o["progress"] > 0 else "SIDE",
                    "open" if o["open"] else "marked"]
            if o["blocked"]:
                tags.append("BLOCKED")
            if o["forbidden"]:
                tags.append("FORBIDDEN")
            lines.append(f"  pass to {o['id']}: {' '.join(tags)} dist={o['dist']:.1f}")
        if choice:
            lines.append(f"  bestPass={choice['id']} type={pass_type(choice, scale)}")
        if target:
            lines.append(f"  dribbleTo=({target[0]:.1f},{target[1]:.1f})")
        return lines
    spot = support_spot(game_state, team_id, my_player_id, position_label)
    if spot:
        return [f"TEAMMATE HAS THE BALL: your supportSpot=({spot[0]:.1f},{spot[1]:.1f}) for the passing triangle"]
    return []
