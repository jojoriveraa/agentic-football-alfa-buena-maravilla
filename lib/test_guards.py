"""Tests for guards.py and pitch-scaled fallback behaviour.

Run directly:  python test_guards.py
"""

import copy

from fallback import FWD1_CONFIG, GK_CONFIG, build_fallback
from guards import fit_to_pitch, fix_mark_target, needs_fallback, pace
from test_helpers import GAME_STATE
from test_state import scaled_state

HOME, AWAY = 0, 1


def _move(x, y):
    return {"commandType": "MOVE_TO", "playerId": 3, "parameters": {"target_x": x, "target_y": y, "sprint": True}}


def test_possession_command_without_ball_is_flagged():
    """The keeper's GK_DISTRIBUTE without the ball must fall back."""
    gk = [{"commandType": "GK_DISTRIBUTE", "playerId": 0, "parameters": {"target_player_id": 1, "method": "THROW"}}]
    assert needs_fallback(gk, GAME_STATE, HOME, 0) == "GK_DISTRIBUTE without the ball"
    # The away player with the holder's index must not count as holding it either.
    shoot = [{"commandType": "SHOOT", "playerId": 3, "parameters": {"aim_location": "TR", "power": 0.9}}]
    assert needs_fallback(shoot, GAME_STATE, AWAY, 3) == "SHOOT without the ball"
    print("=== GUARD: possession commands without the ball flagged ===")


def test_holder_and_off_ball_commands_pass():
    shoot = [{"commandType": "SHOOT", "playerId": 3, "parameters": {"aim_location": "TR", "power": 0.9}}]
    assert needs_fallback(shoot, GAME_STATE, HOME, 3) is None
    assert needs_fallback([_move(1, 1)], GAME_STATE, HOME, 0) is None
    print("=== GUARD: holder may shoot, MOVE_TO always allowed ===")


def test_chasing_our_own_ball_is_flagged():
    """Home player 3 holds the ball: home pressing is pointless, away pressing is fine."""
    press = [{"commandType": "PRESS_BALL", "playerId": 1, "parameters": {"intensity": 0.7}}]
    assert needs_fallback(press, GAME_STATE, HOME, 1) == "PRESS_BALL while our team has the ball"
    assert needs_fallback(press, GAME_STATE, AWAY, 1) is None
    loose = copy.deepcopy(GAME_STATE)
    loose["ball"]["isFree"] = True
    assert needs_fallback(press, loose, HOME, 1) is None
    print("=== GUARD: no pressing or intercepting our own ball ===")


def test_reference_units_on_small_pitch_are_rescaled():
    """x=35 on a ±6.6 pitch means the model reasoned in reference units."""
    gs = scaled_state(0.12)
    cmds = [_move(35.0, -10.0)]
    notes = fit_to_pitch(cmds, gs)
    assert cmds[0]["parameters"]["target_x"] == 4.2 and cmds[0]["parameters"]["target_y"] == -1.2, cmds
    assert notes
    print("=== FIT: reference-unit target rescaled onto the small pitch ===")


def test_slightly_outside_is_clamped():
    gs = scaled_state(0.12)
    cmds = [_move(8.0, 0.5)]
    fit_to_pitch(cmds, gs)
    assert cmds[0]["parameters"]["target_x"] == 6.6, cmds
    print("=== FIT: near-miss target clamped to the goal line ===")


def test_reference_pitch_targets_untouched():
    cmds = [_move(35.0, -10.0)]
    assert fit_to_pitch(cmds, GAME_STATE) == []
    assert cmds[0]["parameters"]["target_x"] == 35.0
    cmds = [_move(70.0, 0.0)]
    fit_to_pitch(cmds, GAME_STATE)
    assert cmds[0]["parameters"]["target_x"] == 55.0
    print("=== FIT: reference pitch only clamps the impossible ===")


def test_fallback_scales_with_pitch():
    """Keeper stays near its own small-pitch goal; forward shoots in range."""
    gs = scaled_state(0.12)
    gk = build_fallback(GK_CONFIG)(gs, HOME, 0)[0]
    assert gk["commandType"] == "MOVE_TO"
    assert abs(gk["parameters"]["target_x"] - (-6.6 * 0.9)) < 0.01, gk
    # Put home forward 3 (the holder) 2 units from the opponent goal at +6.6.
    gs2 = copy.deepcopy(gs)
    for p in gs2["players"]:
        if p["agentId"] == "agentId_3" and p["teamCode"] == "home":
            p["position"] = {"x": 4.6, "y": 0.0}
    gs2["ball"]["position"] = {"x": 4.6, "y": 0.0, "z": 0}
    fwd = build_fallback(FWD1_CONFIG)(gs2, HOME, 3)[0]
    assert fwd["commandType"] == "SHOOT", fwd
    print("=== FALLBACK: positions and shooting range follow the pitch ===")


def _tired(stamina, ball_x):
    """Home FWD 3 with the given stamina; ball at ball_x on the small pitch."""
    gs = scaled_state(0.12)
    for p in gs["players"]:
        if p["agentId"] == "agentId_3" and p["teamCode"] == "home":
            p["stamina"] = stamina
    gs["ball"]["position"] = {"x": ball_x, "y": 0.0, "z": 0}
    gs["ball"]["isFree"] = True
    return gs


def test_tired_player_stops_sprinting():
    gs = _tired(0.1, 2.0)
    cmds = [_move(1.0, 1.0), {"commandType": "PRESS_BALL", "playerId": 3, "parameters": {"intensity": 0.8}}]
    notes = pace(cmds, gs, HOME, 3)
    assert cmds[0]["parameters"]["sprint"] is False and cmds[1]["parameters"]["intensity"] == 0.45, cmds
    assert len(notes) == 2
    fresh = [_move(1.0, 1.0)]
    assert pace(fresh, _tired(0.9, 2.0), HOME, 3) == [] and fresh[0]["parameters"]["sprint"] is True
    near_goal = [_move(-5.0, 0.0)]
    assert pace(near_goal, _tired(0.1, -6.0), HOME, 3) == [] and near_goal[0]["parameters"]["sprint"] is True
    print("=== PACE: tired players walk, except with the ball near our goal ===")


def test_mark_points_at_a_real_outfield_opponent():
    gs = scaled_state(0.12)
    for target in (0, 5):
        cmds = [{"commandType": "MARK", "playerId": 1, "parameters": {"target_player_id": target, "tightness": "TIGHT"}}]
        notes = fix_mark_target(cmds, gs, HOME)
        new = cmds[0]["parameters"]["target_player_id"]
        assert new not in (0, 5) and notes, (target, cmds)
    ok = [{"commandType": "MARK", "playerId": 1, "parameters": {"target_player_id": 3, "tightness": "TIGHT"}}]
    assert fix_mark_target(ok, gs, HOME) == [] and ok[0]["parameters"]["target_player_id"] == 3
    print("=== MARK: keeper or missing targets become the opponent closest to our goal ===")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} tests passed")
