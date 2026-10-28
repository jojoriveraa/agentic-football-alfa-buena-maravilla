"""Coach tools behind the AgentCore Gateway: manage the lessons the agents
read at kickoff and review the goals the keeper recorded.

Packaged with lib/team_memory.py by tools/crear_componentes.py. The Gateway
sends the tool arguments as the event and the tool name in the context as
"<target>___<tool>".
"""

import json

import team_memory


def lambda_handler(event, context):
    tool = context.client_context.custom["bedrockAgentCoreToolName"].split("___")[-1]
    if tool == "listar_lecciones":
        return {"lecciones": team_memory.list_lessons()}
    if tool == "guardar_leccion":
        text, pos = (event.get("texto") or "").strip(), event.get("pos") or "all"
        if not text or len(text) > 300:
            return {"error": "texto: 1 a 300 caracteres, una instruccion concreta en ingles"}
        if pos not in ("all", "gk", "def", "mid", "fwd1", "fwd2"):
            return {"error": "pos: all, gk, def, mid, fwd1 o fwd2"}
        e = team_memory.save(text, team_memory.LESSONS_SESSION, {"pos": pos})["event"]
        return {"guardada": e["eventId"], "pos": pos}
    if tool == "borrar_leccion":
        team_memory.client().delete_event(memoryId=team_memory.MEMORY_ID, actorId=team_memory.ACTOR,
                                          sessionId=team_memory.LESSONS_SESSION, eventId=event["id"])
        return {"borrada": event["id"]}
    if tool == "listar_goles":
        resp = team_memory.client().list_events(
            memoryId=team_memory.MEMORY_ID, actorId=team_memory.ACTOR,
            sessionId=team_memory.GOALS_SESSION, includePayloads=True, maxResults=100)
        goles = []
        for e in sorted(resp.get("events", []), key=lambda e: e["eventTimestamp"]):
            g = json.loads(team_memory.event_text(e))
            g["partido"] = (e.get("metadata") or {}).get("partido", {}).get("stringValue")
            goles.append(g)
        return {"goles": goles}
    return {"error": f"herramienta desconocida: {tool}"}
