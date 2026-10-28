"""Tests for tactics.py and guards.tactical_override on the small live pitch.

Run directly:  python test_tactics.py

Scenarios are built by hand on a pitch with keepers at x=±6 (scale 0.12,
goals at ±6.6), the size the live server has been using.
"""

import math

from guards import tactical_override
from tactics import (
    on_ball_decision,
    pass_options,
    support_spot,
    team_play_lines,
)

HOME, AWAY = 0, 1


def make_state(home: dict, away: dict, holder=None) -> dict:
    """home/away map player index → (x, y); holder is ("home"|"away", index)."""
    players = []
    for code, team in (("home", home), ("away", away)):
        for idx, (x, y) in team.items():
            players.append({"agentId": f"agentId_{idx}", "teamCode": code,
                            "position": {"x": x, "y": y}, "stamina": 0.9})
    ball = {"position": {"x": 0, "y": 0, "z": 0}, "isFree": holder is None}
    if holder:
        code, idx = holder
        team = home if code == "home" else away
        x, y = team[idx]
        ball = {"position": {"x": x, "y": y, "z": 0}, "isFree": False,
                "possessionAgentId": f"agentId_{idx}"}
        # agentId is shared across teams; the fixture keeps the holder first
        players.sort(key=lambda p: not (p["teamCode"] == code and p["agentId"] == f"agentId_{idx}"))
    return {"playMode": "OPEN_PLAY", "gameTime": 30, "score": {"home": 0, "away": 0},
            "ball": ball, "players": players, "teamChat": []}


KEEPERS_HOME = {0: (-6.0, 0.0)}
KEEPERS_AWAY = {0: (6.0, 0.0)}


def test_keeper_clears_long_to_most_advanced():
    gs = make_state({**KEEPERS_HOME, 1: (-4, 1), 2: (-1, -1), 3: (2, 1.5), 4: (3, -1)},
                    {**KEEPERS_AWAY, 1: (-5, 0.5), 2: (0, 0), 3: (1, 2.5), 4: (4, 2)},
                    holder=("home", 0))
    cmd = on_ball_decision(gs, HOME, 0, "GK")
    assert cmd["commandType"] == "GK_DISTRIBUTE" and cmd["parameters"]["method"] == "KICK", cmd
    assert cmd["parameters"]["target_player_id"] == 4, cmd  # most advanced and open
    throw = [{"commandType": "GK_DISTRIBUTE", "playerId": 0,
              "parameters": {"target_player_id": 1, "method": "THROW"}}]
    assert tactical_override(throw, gs, HOME, 0, "GK") == "goalkeeper clears long"
    assert tactical_override([cmd], gs, HOME, 0, "GK") is None
    print("=== GK: long KICK to the most advanced open teammate ===")


def test_no_back_pass_in_own_third():
    # Home defender with the ball near our goal; MID is behind, FWD ahead and open.
    gs = make_state({**KEEPERS_HOME, 1: (-4.5, 0), 2: (-5.5, 2), 3: (-1, 0.5), 4: (2, -2)},
                    {**KEEPERS_AWAY, 1: (-2.5, 2.5), 2: (1, 2), 3: (3, 0), 4: (4, -2.5)},
                    holder=("home", 1))
    options = {o["id"]: o for o in pass_options(gs, HOME, 1)}
    assert options[2]["forbidden"] and options[0]["forbidden"], options
    back = [{"commandType": "PASS", "playerId": 1, "parameters": {"target_player_id": 2, "type": "GROUND"}}]
    assert tactical_override(back, gs, HOME, 1, "DEF") == "back pass in our defensive third"
    to_keeper = [{"commandType": "PASS", "playerId": 1, "parameters": {"target_player_id": 0, "type": "GROUND"}}]
    assert tactical_override(to_keeper, gs, HOME, 1, "DEF") == "back pass in our defensive third"
    cmd = on_ball_decision(gs, HOME, 1, "DEF")
    assert cmd["commandType"] == "PASS" and cmd["parameters"]["target_player_id"] in (3, 4), cmd
    print("=== OWN THIRD: back passes (incl. to the keeper) replaced by a forward pass ===")


def test_back_pass_allowed_in_midfield():
    gs = make_state({**KEEPERS_HOME, 1: (-2, 0), 2: (0.5, 1), 3: (3, 0.5), 4: (2, -2)},
                    {**KEEPERS_AWAY, 1: (4, 2.5), 2: (4, -2.5), 3: (5, 0), 4: (-5, 2.5)},
                    holder=("home", 2))
    back = [{"commandType": "PASS", "playerId": 2, "parameters": {"target_player_id": 1, "type": "GROUND"}}]
    assert tactical_override(back, gs, HOME, 2, "MID") is None
    print("=== MIDFIELD: a back pass away from our goal is the model's call ===")


def test_self_pass_under_pressure():
    # Home forward with an opponent right in front and every teammate marked.
    gs = make_state({**KEEPERS_HOME, 1: (-3, 0), 2: (-1, 2), 3: (1, 0), 4: (2, -2.5)},
                    {**KEEPERS_AWAY, 1: (1.6, 0), 2: (-3.4, 0), 3: (-1.3, 2.3), 4: (2.3, -2.2)},
                    holder=("home", 3))
    shoot_far = [{"commandType": "SHOOT", "playerId": 3, "parameters": {"aim_location": "TR", "power": 0.9}}]
    assert tactical_override(shoot_far, gs, HOME, 3, "FWD1") == "under pressure, carry the ball"
    cmd = on_ball_decision(gs, HOME, 3, "FWD1")
    assert cmd["commandType"] == "MOVE_TO" and cmd["parameters"]["sprint"] is True, cmd
    tx, ty = cmd["parameters"]["target_x"], cmd["parameters"]["target_y"]
    assert tx > 1.0, "the self-pass goes forward"
    assert math.dist((tx, ty), (1.6, 0)) > math.dist((1, 0), (1.6, 0)), "and away from the defender"
    print(f"=== PRESSURE: carries the ball into space at ({tx}, {ty}) ===")


def test_shot_in_range_survives_pressure():
    gs = make_state({**KEEPERS_HOME, 1: (-3, 0), 2: (0, 1), 3: (4.5, 0), 4: (2, -2)},
                    {**KEEPERS_AWAY, 1: (5.0, 0.4), 2: (1, 2), 3: (-2, -2), 4: (3, 2.5)},
                    holder=("home", 3))
    shoot = [{"commandType": "SHOOT", "playerId": 3, "parameters": {"aim_location": "TR", "power": 0.9}}]
    assert tactical_override(shoot, gs, HOME, 3, "FWD1") is None
    print("=== PRESSURE: a shot in range is kept ===")


def test_triangle_spots_home_and_away():
    home = {**KEEPERS_HOME, 1: (-4, 0), 2: (0, 0), 3: (1, 2), 4: (1, -2)}
    away = {**KEEPERS_AWAY, 1: (3, 0), 2: (2, 2), 3: (-2, 1), 4: (-2, -1)}
    gs = make_state(home, away, holder=("home", 2))
    f1, f2 = support_spot(gs, HOME, 3, "FWD1"), support_spot(gs, HOME, 4, "FWD2")
    assert f1 == (1.68, 1.2) and f2 == (1.68, -1.2), (f1, f2)  # ahead-left / ahead-right
    d = support_spot(gs, HOME, 1, "DEF")
    assert d[0] < 0, d
    assert support_spot(gs, HOME, 2, "MID") is None, "the carrier has no support spot"
    assert support_spot(gs, HOME, 0, "GK") is None
    # Same shape for AWAY, mirrored: ahead is -x and left is -y.
    gs_away = make_state(home, away, holder=("away", 1))
    a1 = support_spot(gs_away, AWAY, 3, "FWD1")
    assert a1 == (round(3 - 1.68, 2), -1.2), a1
    print("=== TRIANGLE: FWD1 ahead-left, FWD2 ahead-right, DEF behind, mirrored for AWAY ===")


def test_defender_safety_stays_in_own_half():
    gs = make_state({**KEEPERS_HOME, 1: (-2, 0), 2: (2, 1), 3: (5, 0), 4: (4, -2)},
                    {**KEEPERS_AWAY, 1: (5.5, 1), 2: (3, 2.5), 3: (-1, 2), 4: (-1, -2)},
                    holder=("home", 3))
    d = support_spot(gs, HOME, 1, "DEF")
    assert d[0] <= 0.0, d
    print("=== TRIANGLE: defender never crosses halfway as the safety ===")


def test_hints_reach_the_prompt():
    home = {**KEEPERS_HOME, 1: (-4, 0), 2: (0, 0), 3: (1, 2), 4: (1, -2)}
    away = {**KEEPERS_AWAY, 1: (0.7, 0), 2: (2, 2), 3: (-2, 1), 4: (-2, -1)}
    gs = make_state(home, away, holder=("home", 2))
    carrier = "\n".join(team_play_lines(gs, HOME, 2, "MID"))
    assert "WITH THE BALL: underPressure=True" in carrier, carrier
    assert "dribbleTo=" in carrier and "pass to 3:" in carrier
    support = team_play_lines(gs, HOME, 3, "FWD1")
    assert support == ["TEAMMATE HAS THE BALL: your supportSpot=(1.7,1.2) for the passing triangle"], support
    assert team_play_lines(make_state(home, away), HOME, 3, "FWD1") == []
    print("=== HINTS: carrier options and support spot rendered ===")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} tests passed")
