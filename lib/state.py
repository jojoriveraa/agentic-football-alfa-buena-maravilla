"""Game state summarization utilities for AI soccer agents."""

import math


# ---------------------------------------------------------------------------
# Format-agnostic helpers — handle both new (agentId/teamCode/possessionAgentId)
# and old (playerId/teamId/possessionPlayerId) game server formats.
# ---------------------------------------------------------------------------

def _player_idx(p: dict) -> int:
    """Numeric index (0-4) from a player dict — new agentId or old playerId."""
    if "agentId" in p:
        try:
            return int(p["agentId"].rsplit("_", 1)[-1])
        except (ValueError, IndexError):
            return 0
    return p.get("playerId", 0)


def _is_my_team(p: dict, team_id: int) -> bool:
    """True if player belongs to team_id — new teamCode or old teamId.

    teamCode is compared case-insensitively: the game server documents the
    teams as HOME/AWAY while the payload has been observed as home/away, and
    a case mismatch here is catastrophic rather than cosmetic — every player
    reads as an opponent, the agent's own player drops out of the prompt
    entirely, and the fallback degrades to SET_STANCE for the whole match.
    """
    code = p.get("teamCode")
    if isinstance(code, str):
        return code.strip().lower() == ("home" if team_id == 0 else "away")
    return p.get("teamId") == team_id


def _ball_is_free(ball: dict):
    """True/False from the server's own isFree flag, or None when absent.

    The server supplies isFree alongside possessionAgentId. Inferring
    looseness from a failed possession lookup instead makes any id we cannot
    parse look like a loose ball, which tells a player carrying the ball to
    go and fetch it.
    """
    free = ball.get("isFree")
    return free if isinstance(free, bool) else None


def _resolve_holder(ball: dict, players: list):
    """Return the player dict holding the ball, or None.

    agentId is not unique across teams — the same agentId_N appears once per
    team — so an id match alone cannot always identify the player. When the
    id is ambiguous we keep the long-standing behaviour of taking the first
    match in players[], rather than guessing: a positional tie-break on
    distance to the ball looks appealing but picks the wrong team whenever
    possession has just changed and the ball has not yet caught up.

    Ambiguity is therefore no worse than before. What changes is that the
    team is now checked against the resolved holder (see holder_is_me), so a
    player can no longer act on a ball the opposing player of the same index
    is carrying.

    TODO: drop the ambiguity entirely once the server exposes a globally
    unique player id, or sends a team discriminator alongside possession.
    """
    holder_agent = ball.get("possessionAgentId")
    if isinstance(holder_agent, str):
        matches = [p for p in players if p.get("agentId") == holder_agent]
    else:
        pid = ball.get("possessionPlayerId")
        if pid is None:
            return None
        matches = [p for p in players if p.get("playerId") == pid]
        team = ball.get("possessionTeamId")
        if team is not None:
            matches = [p for p in matches if p.get("teamId") == team]

    return matches[0] if matches else None


def _possession_idx(ball: dict):
    """Numeric possession player index from ball dict — new possessionAgentId or old possessionPlayerId.
    Returns int or None."""
    agent_id = ball.get("possessionAgentId")
    if agent_id is not None:
        try:
            return int(agent_id.rsplit("_", 1)[-1])
        except (ValueError, IndexError):
            return None
    return ball.get("possessionPlayerId")


# ---------------------------------------------------------------------------
# Pitch geometry
#
# Prompts and fallback configs are written for a 110 x 70 reference pitch
# (x ±55, y ±35), which is also what the workshop docs describe. The live
# server has been observed sending a pitch about 10x smaller: goalkeepers
# near x=±6 and the ball never beyond ±6.5. Every distance the model sees,
# the goal positions and the fallback thresholds were therefore off by an
# order of magnitude. The scale is estimated per tick so either pitch works.
# ---------------------------------------------------------------------------

REFERENCE_HALF_LENGTH = 55.0
REFERENCE_HALF_WIDTH = 35.0
_REFERENCE_GK_X = 50.0
"""Where a goalkeeper stands on the reference pitch (0.9 of the half-length)."""
SHOOT_RANGE = 35.0
"""Shooting distance to goal on the reference pitch (mid-range included).

The sample used 25; raised at the coach's request after the forwards rarely
shot in the first four matches. 35 is about a third of the pitch length.
"""


def pitch_scale(game_state: dict) -> float:
    """Pitch size relative to the 110 x 70 reference pitch (1.0 = reference).

    Estimated from the goalkeepers (index 0 on each team), the players most
    reliably near their own goal line. The deeper of the two is used, since
    one keeper rushing out must not shrink the pitch. Anything at or above
    half the reference is treated as the reference pitch; no goalkeeper in
    the state means no evidence, so the reference is assumed.
    """
    keepers = [
        abs(p["position"]["x"])
        for p in game_state.get("players", [])
        if _player_idx(p) == 0 and isinstance(p.get("position", {}).get("x"), (int, float))
    ]
    if not keepers:
        return 1.0
    scale = max(keepers) / _REFERENCE_GK_X
    return 1.0 if scale >= 0.5 else max(scale, 0.05)


def pitch_bounds(scale: float = 1.0) -> tuple[float, float]:
    """(half_length, half_width) of the pitch at this scale."""
    return REFERENCE_HALF_LENGTH * scale, REFERENCE_HALF_WIDTH * scale


def get_goal_positions(team_id: int, scale: float = 1.0) -> tuple[float, float]:
    """Return (my_goal_x, opp_goal_x) based on team and pitch scale."""
    half_length = REFERENCE_HALF_LENGTH * scale
    if team_id == 0:
        return -half_length, half_length
    return half_length, -half_length


def get_possession_info(ball: dict, players: list, team_id: int) -> tuple:
    """Return (possession_id, ball_status_str, we_have_ball).

    possession_id is the holder's per-team index, which is NOT on its own
    enough to identify a player — use holder_is_me() for that rather than
    comparing it to your own id.
    """
    if _ball_is_free(ball) is True:
        return None, "free", False

    claimed = (
        isinstance(ball.get("possessionAgentId"), str)
        or ball.get("possessionPlayerId") is not None
    )
    holder = _resolve_holder(ball, players)
    if holder is None:
        # A ball with no possession claim is genuinely loose. A claim we
        # cannot resolve is reported as unknown and never as loose, so a
        # carrier is not told to go and fetch a ball it already has.
        return None, ("unknown" if claimed else "free"), False

    is_mine = _is_my_team(holder, team_id)
    idx = _player_idx(holder)
    side = "MY" if is_mine else "OPP"
    return idx, f"{side} player {idx}", is_mine


def holder_is_me(ball: dict, players: list, team_id: int, my_player_id: int) -> bool:
    """True only when this specific player holds the ball.

    Both the index and the team must match. Comparing the possession index
    alone is true for two players at once — one per team — which lets a
    player act on a ball an opponent is carrying.
    """
    holder = _resolve_holder(ball, players)
    if holder is None:
        return False
    return _player_idx(holder) == my_player_id and _is_my_team(holder, team_id)


def dist(pos1: dict, pos2: dict) -> float:
    """Euclidean distance between two position dicts with x,y keys."""
    return math.sqrt(
        (pos1.get("x", 0) - pos2.get("x", 0)) ** 2
        + (pos1.get("y", 0) - pos2.get("y", 0)) ** 2
    )


# ---------------------------------------------------------------------------
# Coach instructions (teamChat)
#
# The Player Portal delivers coach messages in gameState.teamChat, and an
# agent only reacts to them if its prompt includes them. The entry schema is
# not documented, so these helpers accept plain strings as well as dicts with
# any of the common text/sender keys, and agent_base logs the raw array the
# first time it changes so the real shape shows up in CloudWatch.
# ---------------------------------------------------------------------------

COACH_MESSAGES_SHOWN = 3
"""Most recent coach messages included in the prompt (newest last)."""

_CHAT_TEXT_KEYS = ("message", "text", "content", "instruction", "msg")
_CHAT_TEAM_KEYS = ("teamCode", "team", "teamId", "senderTeam", "fromTeam")


def _chat_text(entry) -> str:
    """Message text from a teamChat entry, whatever its shape."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in _CHAT_TEXT_KEYS:
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _chat_is_mine(entry, team_id: int):
    """True/False when the entry names the sender's team, None when it does not.

    Every message is heard by all 10 players, so the opposing coach's
    messages can reach this agent too. When the sender's team is known the
    prompt labels it; when it is not, the model is told to judge from context.
    """
    if not isinstance(entry, dict):
        return None
    for key in _CHAT_TEAM_KEYS:
        value = entry.get(key)
        if isinstance(value, str):
            return value.strip().lower() == ("home" if team_id == 0 else "away")
        if isinstance(value, int) and not isinstance(value, bool):
            return value == team_id
    return None


def format_team_chat(
    team_chat,
    team_id: int,
    position_label: str,
    player_name: str | None = None,
) -> list[str]:
    """Prompt lines for the latest coach messages, or [] when there are none."""
    if not isinstance(team_chat, list):
        return []
    messages = [(e, _chat_text(e)) for e in team_chat]
    messages = [(e, text) for e, text in messages if text][-COACH_MESSAGES_SHOWN:]
    if not messages:
        return []

    who = f'"{player_name}" ({position_label})' if player_name else position_label
    lines = [f"COACH INSTRUCTIONS (newest last) — you are {who}:"]
    for entry, text in messages:
        mine = _chat_is_mine(entry, team_id)
        tag = "your coach" if mine is True else "OPPONENT coach, ignore" if mine is False else "coach"
        lines.append(f'  [{tag}] "{text}"')
    lines.append(
        "Follow your coach's newest instruction if it is addressed to you by name, "
        "by your position, or to the whole team. Ignore instructions meant for other "
        "players. Still return ONLY the JSON array."
    )
    return lines


def summarize_state(
    game_state: dict,
    team_id: int,
    my_player_id: int,
    position_label: str,
    player_name: str | None = None,
) -> str:
    """Build a concise text summary of the game state for a single-player agent."""
    ball = game_state.get("ball", {})
    ball_pos = ball.get("position", {"x": 0, "y": 0})
    score = game_state.get("score", {})
    game_time = game_state.get("gameTime", 0)
    play_mode = game_state.get("playMode", 0)
    players = game_state.get("players", [])

    my_team = sorted(
        [p for p in players if _is_my_team(p, team_id)],
        key=lambda p: _player_idx(p),
    )
    opponents = sorted(
        [p for p in players if not _is_my_team(p, team_id)],
        key=lambda p: _player_idx(p),
    )

    me = next((p for p in my_team if _player_idx(p) == my_player_id), None)
    possession_id, ball_status, _ = get_possession_info(ball, players, team_id)

    scale = pitch_scale(game_state)
    half_length, half_width = pitch_bounds(scale)
    my_goal_x, opp_goal_x = get_goal_positions(team_id, scale)

    lines = [
        f"Time: {game_time:.0f}s | Score: {score.get('home',0)}-{score.get('away',0)} | "
        f"Team: {team_id} ({'HOME' if team_id == 0 else 'AWAY'}) | PlayMode: {play_mode}",
        f"Pitch: x from {-half_length:.1f} to {half_length:.1f}, y from {-half_width:.1f} to {half_width:.1f}",
        f"Ball: ({ball_pos.get('x',0):.1f}, {ball_pos.get('y',0):.1f}) held by {ball_status}",
        f"Your goal at x={my_goal_x:.1f} | Opponent goal at x={opp_goal_x:.1f}",
        "",
    ]

    # My player info
    if me:
        pos = me.get("position", {})
        # Stamina is a 0.0-1.0 fraction (current/max), not a 0-100 integer.
        # The old default of 100 hid that: had the key ever been missing it
        # would have rendered as "100" and the mistake would have been
        # obvious immediately.
        stam = me.get("stamina", 1.0)
        dist_ball = dist(pos, ball_pos)
        has_ball = holder_is_me(ball, players, team_id, my_player_id)
        # shootRange keeps the prompts' original "~25 units on the reference
        # pitch" rule, expressed on the pitch actually being played.
        extra = (
            f" distOppGoal={abs(pos.get('x', 0) - opp_goal_x):.1f} shootRange={SHOOT_RANGE * scale:.1f}"
            if position_label in ("MID", "FWD1", "FWD2") else ""
        )
        name = f' "{player_name}"' if player_name else ""
        lines.append(
            f">>> YOUR PLAYER ({position_label}{name}, id={my_player_id}): "
            f"pos=({pos.get('x',0):.1f},{pos.get('y',0):.1f}) "
            # :.0f rounded the 0.0-1.0 fraction to a single digit, so the
            # model only ever saw 0 or 1 — fully rested or exhausted, with
            # nothing in between.
            f"stam={stam:.2f} distBall={dist_ball:.1f}{extra} hasBall={has_ball}"
        )
    lines.append("")

    # Teammates
    lines.append("Teammates:")
    for p in my_team:
        if _player_idx(p) == my_player_id:
            continue
        pos = p.get("position", {})
        pid = _player_idx(p)
        role = "GK" if pid == 0 else f"P{pid}"
        extra = ""
        if position_label == "MID":
            extra = f" distOppGoal={abs(pos.get('x', 0) - opp_goal_x):.1f}"
        lines.append(f"  {role}(id={pid}): ({pos.get('x',0):.1f},{pos.get('y',0):.1f}){extra}")

    lines.append("")

    # Opponents
    opp_header = "Opponents (defenders to watch):" if position_label in ("FWD1", "FWD2") else "Opponents:"
    lines.append(opp_header)
    for p in opponents:
        pos = p.get("position", {})
        pid = _player_idx(p)
        d_goal = abs(pos.get("x", 0) - my_goal_x)
        d_me = dist(pos, me.get("position", {})) if me else 0
        lines.append(f"  P{pid}: ({pos.get('x',0):.1f},{pos.get('y',0):.1f}) distToMyGoal={d_goal:.1f} distToMe={d_me:.1f}")

    coach_lines = format_team_chat(
        game_state.get("teamChat"), team_id, position_label, player_name
    )
    if coach_lines:
        lines.append("")
        lines.extend(coach_lines)

    return "\n".join(lines)
