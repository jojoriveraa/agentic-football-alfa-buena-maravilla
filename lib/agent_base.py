"""Base agent factory for AI soccer position agents."""

import json
import re
from typing import Callable
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import BedrockModel

from parsing import parse_commands
import coach_guard
import team_memory
from state import _chat_text, summarize_state
from fallback import FallbackConfig, build_last_resort
from guards import fit_to_pitch, fix_mark_target, needs_fallback, pace, tactical_override
from state import holder_is_me
from tactics import (chase_move, first_defender, keeper_move, keeper_off_ball, on_ball_decision,
                     support_move, team_play_lines)


def create_agent(
    system_prompt: str,
    model_id: str = "us.amazon.nova-micro-v1:0",
    history_messages: int = 2,
) -> Agent:
    """Create a Strands Agent with the given system prompt.

    history_messages bounds how much of the match the model re-reads each
    tick. The Strands default (40 messages) resent ~20 stale game states per
    call, which cost latency and made the model copy its own earlier answers:
    the goalkeeper repeated GK_DISTRIBUTE tick after tick without the ball.
    Coach messages do not depend on history; they arrive in every state.
    """
    model = BedrockModel(model_id=model_id)
    return Agent(
        model=model,
        system_prompt=system_prompt,
        conversation_manager=SlidingWindowConversationManager(window_size=history_messages),
    )


def parse_prompt(payload) -> dict:
    """Parse the invocation payload's prompt, accepting either str or dict.

    The main path has always accepted both, but the error handler used to
    re-parse with a bare json.loads(), which raises TypeError on a dict. That
    exception was caught by the outer bare except, so the rule-based fallback
    was skipped entirely and control fell through to the last resort.
    """
    prompt = payload.get("prompt", "{}")
    if isinstance(prompt, dict):
        return prompt
    try:
        parsed = json.loads(prompt)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def team_id_from(prompt_data: dict, log, label: str) -> int:
    """teamId from the payload; HOME only as a loudly logged last resort.

    teamId is authoritative input, not a guessable default. Silently
    defaulting to 0 makes an away agent emit commands tagged HOME, which the
    server attributes to the opposing team — and leaves nothing in the logs
    to explain why the away side played as though it were the home side.
    """
    team_id = prompt_data.get("teamId")
    if team_id is None:
        log.error(
            f"{label}: payload contained no teamId; falling back to HOME (0). "
            "Commands this tick may be attributed to the wrong team."
        )
        return 0
    return team_id


def create_invoke_handler(
    app,
    agent: Agent,
    my_player_id: int,
    position_label: str,
    fallback_fn: Callable[[dict, int, int], list[dict]],
    fallback_cfg: FallbackConfig,
    player_name: str | None = None,
    team_play: bool = False,
):
    """Create and register the @app.entrypoint invoke handler.

    Three layers of error handling, from best to worst:
      1. LLM response → parse into commands
      2. fallback_fn(game_state, team_id, my_player_id) → rule-based commands
      3. last-resort command from fallback_cfg → single safe command

    player_name should match the name set for this player on the Player
    Portal's My Team page, so coach messages addressed by name reach it.

    team_play turns on tactics.py: triangle and passing hints in the state,
    the team-play rules in guards.tactical_override(), and team-play
    replacements whenever a model command is overridden.
    """
    log = app.logger
    last_resort = build_last_resort(fallback_cfg, my_player_id)
    # teamChat's entry schema is undocumented: log the raw array whenever it
    # changes, so the first match with coaching shows its real shape.
    last_chat = {"raw": None}

    def replacement(game_state, team_id, pid):
        """What to play instead of an overridden or unparseable command."""
        if team_play:
            if holder_is_me(game_state.get("ball", {}), game_state.get("players", []), team_id, pid):
                return [on_ball_decision(game_state, team_id, pid, position_label)]
            if position_label == "GK":
                return [keeper_move(game_state, team_id, pid)]
            if first_defender(game_state, team_id) == pid:
                return [chase_move(game_state, team_id, pid)]
            support = support_move(game_state, team_id, pid, position_label)
            if support:
                return [support]
        return fallback_fn(game_state, team_id, pid)

    @app.entrypoint
    async def invoke(payload, context):
        # Parsed once, outside the try, so the error handler below can reuse
        # it instead of re-parsing and throwing a second exception.
        prompt_data = parse_prompt(payload)
        team_id = team_id_from(prompt_data, log, position_label)
        try:
            game_state = prompt_data.get("gameState", {})

            # Honor myPlayers from payload if present, otherwise use configured player ID
            my_players = prompt_data.get("myPlayers", [my_player_id])
            effective_pid = my_players[0] if my_players else my_player_id

            # The ball's possession fields decide who "has the ball" and the
            # holder id is ambiguous across teams; log their raw shape once
            # per new key set in case the server adds a team discriminator.
            ball_keys = sorted((game_state.get("ball") or {}).keys())
            if ball_keys != last_chat.get("ball_keys"):
                last_chat["ball_keys"] = ball_keys
                log.info(f"{position_label} ball fields: {json.dumps(game_state.get('ball'), default=str)[:500]}")

            team_chat = game_state.get("teamChat")
            if team_chat:
                raw_chat = json.dumps(team_chat, default=str)
                if raw_chat != last_chat["raw"]:
                    last_chat["raw"] = raw_chat
                    log.info(f"{position_label} teamChat changed: {raw_chat[:1000]}")
                if coach_guard.enabled():
                    kept = [e for e in team_chat if coach_guard.allowed(_chat_text(e), log)]
                    if len(kept) != len(team_chat):
                        game_state = {**game_state, "teamChat": kept}

            # One writer per team: the keeper records each goal in Memory.
            score = game_state.get("score")
            if position_label == "GK" and score:
                if last_chat.get("score") not in (None, score):
                    match = re.sub(r"-pos\d+$", "", getattr(context, "session_id", None) or "sin-sesion")
                    team_memory.record_goal(game_state, team_id, match, log)
                last_chat["score"] = score

            # The keeper only needs the model when it holds the ball.
            if team_play and position_label == "GK" and not holder_is_me(
                    game_state.get("ball", {}), game_state.get("players", []), team_id, effective_pid):
                log.info(f"{position_label} agent invoked for team {team_id}, controlling player {effective_pid}")
                commands = [keeper_off_ball(game_state, team_id, effective_pid)]
                fit_to_pitch(commands, game_state)
                log.info(f"Rule returned {len(commands)} commands: {[c.get('commandType') for c in commands]}")
                yield json.dumps(commands)
                return

            state_summary = summarize_state(
                game_state, team_id, effective_pid, position_label, player_name
            )
            if team_play:
                hints = team_play_lines(game_state, team_id, effective_pid, position_label)
                if hints:
                    state_summary += "\n\n" + "\n".join(hints)
            lessons = team_memory.lessons_for(position_label, log)
            if lessons:
                state_summary += "\n\nLESSONS FROM PREVIOUS MATCHES (your coach saved these):\n" + \
                    "\n".join(f"- {l}" for l in lessons)

            log.info(f"{position_label} agent invoked for team {team_id}, controlling player {effective_pid}")

            response = agent(state_summary)
            response_text = str(response)

            def on_recovered(raw: str) -> None:
                # The model wrote Python-flavoured JSON (usually `True`/`False`/`None`).
                # We recovered it rather than dropping the command and falling back —
                # logged so you can see how often your model does this.
                log.warn(f"{position_label} recovered malformed JSON from the model: {raw[:200]}")

            commands = parse_commands(response_text, team_id, effective_pid, on_recovered)

            if commands:
                log.info(f"LLM returned {len(commands)} commands: "
                         f"{[c.get('commandType') for c in commands]}")
                reason = needs_fallback(commands, game_state, team_id, effective_pid)
                if not reason and team_play:
                    reason = tactical_override(
                        commands, game_state, team_id, effective_pid, position_label
                    )
                if reason:
                    commands = replacement(game_state, team_id, effective_pid)
                    log.warn(f"{position_label} overriding model ({reason}), playing "
                             f"{[c.get('commandType') for c in commands]}")
            else:
                log.warn(f"LLM parse failed, using fallback. Response: {response_text[:200]}")
                commands = replacement(game_state, team_id, effective_pid)
                log.info(f"Fallback returned {len(commands)} commands")

            for note in fit_to_pitch(commands, game_state):
                log.info(f"{position_label} target fitted to pitch: {note}")
            for note in fix_mark_target(commands, game_state, team_id) + \
                    pace(commands, game_state, team_id, effective_pid):
                log.info(f"{position_label} effort adjusted: {note}")
            yield json.dumps(commands)

        except Exception as e:
            log.error(f"{position_label} agent error: {e}")
            try:
                # Reuse the payload parsed at entry. Re-parsing here is what
                # broke this path: json.loads() on an already-decoded dict
                # raises TypeError, the bare except below swallowed it, and
                # the rule-based fallback never ran.
                my_players = prompt_data.get("myPlayers", [my_player_id])
                effective_pid = my_players[0] if my_players else my_player_id
                commands = fallback_fn(
                    prompt_data.get("gameState", {}),
                    team_id,
                    effective_pid,
                )
                yield json.dumps(commands)
            except Exception as fallback_error:
                log.error(f"{position_label} fallback also failed: {fallback_error}")
                cmd = dict(last_resort)
                # team_id came from the payload at entry, so the last resort
                # is no longer hardcoded to HOME.
                cmd["teamId"] = team_id
                yield json.dumps([cmd])

    return invoke
