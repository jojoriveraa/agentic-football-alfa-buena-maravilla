"""Lessons between matches, kept in AgentCore Memory.

The coach saves short lessons after a match with tools/lecciones.py; each
agent reads them once, on the first tick of the match, and shows them to the
model on every tick after that. Strategy can change between matches without
a redeploy, and a match pays the Memory latency once instead of every tick.

The keeper also records every goal (score change) as an event, off the
request thread, so goals can be reviewed across matches.

When no memory is configured (lib/agentcore_resources.json, written by
tools/crear_componentes.py) there are no lessons and nothing is recorded.
"""

import json
import os
import threading
from datetime import datetime, timezone

_RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agentcore_resources.json")
ACTOR = "equipo"
LESSONS_SESSION = "lecciones"
GOALS_SESSION = "goles"
MAX_LESSONS = 6

_client = None
_lessons: list[dict] | None = None


def _memory_id():
    try:
        with open(_RESOURCES) as f:
            return json.load(f).get("memory_id")
    except (OSError, ValueError):
        return None


# The coach-tools Lambda behind the Gateway gets the id as an env var.
MEMORY_ID = os.environ.get("AFWC_MEMORY_ID") or _memory_id()


def client():
    """bedrock-agentcore data plane, with short timeouts: a slow Memory call
    must never cost a tick."""
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client("bedrock-agentcore", config=Config(
            connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}))
    return _client


def event_text(event) -> str:
    for item in event.get("payload", []):
        text = (item.get("conversational") or {}).get("content", {}).get("text")
        if text:
            return text
    return ""


def event_pos(event) -> str:
    return ((event.get("metadata") or {}).get("pos") or {}).get("stringValue", "all")


def list_lessons() -> list[dict]:
    """Every saved lesson, oldest first: {"id", "pos", "text"}."""
    events, token = [], None
    while True:
        kwargs = {"nextToken": token} if token else {}
        resp = client().list_events(memoryId=MEMORY_ID, actorId=ACTOR, sessionId=LESSONS_SESSION,
                                    includePayloads=True, maxResults=100, **kwargs)
        events += resp.get("events", [])
        token = resp.get("nextToken")
        if not token:
            break
    events.sort(key=lambda e: e.get("eventTimestamp"))
    return [{"id": e["eventId"], "pos": event_pos(e), "text": event_text(e)} for e in events if event_text(e)]


def lessons_for(position_label: str, log=None) -> list[str]:
    """Lessons for this position, read from Memory once per match (each match
    runs in a fresh runtime session). Fails closed to no lessons."""
    global _lessons
    if not MEMORY_ID:
        return []
    if _lessons is None:
        try:
            _lessons = list_lessons()
            if log:
                log.info(f"{position_label} loaded {len(_lessons)} lessons from memory")
        except Exception as e:  # noqa: BLE001 - no lessons beats a lost tick
            _lessons = []
            if log:
                log.warn(f"{position_label} memory unavailable, playing without lessons: {e}")
    pos = position_label.lower()
    mine = [l["text"] for l in _lessons if l["pos"] in ("all", pos)]
    return mine[-MAX_LESSONS:]


def save(text: str, session: str, metadata: dict[str, str]):
    return client().create_event(
        memoryId=MEMORY_ID, actorId=ACTOR, sessionId=session,
        eventTimestamp=datetime.now(timezone.utc),
        payload=[{"conversational": {"content": {"text": text}, "role": "USER"}}],
        metadata={k: {"stringValue": v} for k, v in metadata.items()},
    )


def record_goal(game_state: dict, team_id: int, match: str, log=None) -> None:
    """Write the state at a goal to Memory in the background."""
    if not MEMORY_ID:
        return
    ball = (game_state.get("ball") or {}).get("position", {})
    snapshot = {
        "gameTime": game_state.get("gameTime"),
        "score": game_state.get("score"),
        "ourTeamId": team_id,
        "ball": [round(ball.get("x", 0), 2), round(ball.get("y", 0), 2)],
        "players": [[p.get("teamCode"), p.get("agentId"),
                     round(p.get("position", {}).get("x", 0), 2), round(p.get("position", {}).get("y", 0), 2)]
                    for p in game_state.get("players", [])],
    }

    def write():
        try:
            save(json.dumps(snapshot), GOALS_SESSION, {"partido": match[:200]})
        except Exception as e:  # noqa: BLE001 - background write, never fatal
            if log:
                log.warn(f"could not record goal in memory: {e}")

    threading.Thread(target=write, daemon=True).start()
