"""Save, list and delete the lessons the agents read at the start of each match.

Lessons live in AgentCore Memory (created by crear_componentes.py). Each agent
reads them on its first tick, so a change applies from the next match on, with
no redeploy. Write them in English and as one concrete instruction each: they
go into the model's prompt as-is.

Usage (repo root, workshop credentials active):

    python3 tools/lecciones.py                                  # list lessons
    python3 tools/lecciones.py add "Shoot from up to 6.5 units when open" --pos mid
    python3 tools/lecciones.py add "Rivals leave a striker near our goal: keep the DEF goal-side of him"
    python3 tools/lecciones.py delete <id>
    python3 tools/lecciones.py goles                            # goals the keeper recorded
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import team_memory  # noqa: E402

POSITIONS = ["all", "gk", "def", "mid", "fwd1", "fwd2"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    add = sub.add_parser("add", help="save a lesson")
    add.add_argument("text")
    add.add_argument("--pos", choices=POSITIONS, default="all", help="who reads it (default: everyone)")
    rm = sub.add_parser("delete", help="delete a lesson by id")
    rm.add_argument("id")
    sub.add_parser("goles", help="list the goals the keeper recorded")
    args = ap.parse_args()

    if not team_memory.MEMORY_ID:
        raise SystemExit("Corre primero tools/crear_componentes.py (falta memory_id).")
    c = team_memory.client()

    if args.cmd == "add":
        if len(args.text) > 300:
            raise SystemExit("Máximo 300 caracteres: cada lección entra completa al prompt.")
        e = team_memory.save(args.text, team_memory.LESSONS_SESSION, {"pos": args.pos})["event"]
        print(f"Guardada {e['eventId']} para {args.pos}.")
    elif args.cmd == "delete":
        c.delete_event(memoryId=team_memory.MEMORY_ID, actorId=team_memory.ACTOR,
                       sessionId=team_memory.LESSONS_SESSION, eventId=args.id)
        print(f"Borrada {args.id}.")
    elif args.cmd == "goles":
        resp = c.list_events(memoryId=team_memory.MEMORY_ID, actorId=team_memory.ACTOR,
                             sessionId=team_memory.GOALS_SESSION, includePayloads=True, maxResults=100)
        for e in sorted(resp.get("events", []), key=lambda e: e["eventTimestamp"]):
            g = json.loads(team_memory.event_text(e))
            match = (e.get("metadata") or {}).get("partido", {}).get("stringValue", "?")
            print(f"{match[:8]}  t={g['gameTime']:.0f}s  marcador={g['score']}  balón={g['ball']}")
    else:
        lessons = team_memory.list_lessons()
        if not lessons:
            print("Sin lecciones guardadas.")
        for l in lessons:
            print(f"{l['id']}  [{l['pos']}]  {l['text']}")


if __name__ == "__main__":
    main()
