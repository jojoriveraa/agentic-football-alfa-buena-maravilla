"""Post-match report for the 5 agents, from their CloudWatch logs.

Usage (from the repo root, with the workshop credentials active):

    python3 tools/analizar_partido.py                 # latest match
    python3 tools/analizar_partido.py --session abc   # match whose session id contains "abc"
    python3 tools/analizar_partido.py --json out.json # also save the numbers

Finds the runtimes named ai_gk_agent, ai_def_agent, ai_mid_agent,
ai_fwd1_agent and ai_fwd2_agent in the current account. Matches are the
log streams whose session id contains "-team" (portal tests use
"scrimmage-drill"). Two sources are read:
  - the agent's own log lines (commands, fallbacks, guard overrides, latency)
  - the OpenTelemetry stream, which holds the exact state each model saw, to
    check decisions against the situation (hasBall, who holds the ball, score).
Only uses read-only calls: bedrock-agentcore-control list and logs reads.
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

import boto3

POSITIONS = ["gk", "def", "mid", "fwd1", "fwd2"]
POSSESSION_COMMANDS = {"PASS", "SHOOT", "GK_DISTRIBUTE"}


def find_runtimes(region):
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    found = {}
    for page in client.get_paginator("list_agent_runtimes").paginate():
        for rt in page["agentRuntimes"]:
            m = re.fullmatch(r"ai_(gk|def|mid|fwd1|fwd2)_agent", rt["agentRuntimeName"])
            if m:
                found[m.group(1)] = rt["agentRuntimeId"]
    return found


def match_stream(logs, group, want):
    pages = logs.get_paginator("describe_log_streams").paginate(
        logGroupName=group, orderBy="LastEventTime", descending=True
    )
    for page in pages:
        for s in page["logStreams"]:
            name = s["logStreamName"]
            if "runtime-logs" in name and want in name:
                return name
    return None


def read_stream(logs, group, stream):
    kwargs = {"logGroupName": group, "logStreamName": stream, "startFromHead": True}
    token = None
    while True:
        if token:
            kwargs["nextToken"] = token
        resp = logs.get_log_events(**kwargs)
        for e in resp["events"]:
            if e["message"].startswith("{"):
                try:
                    yield e["timestamp"], json.loads(e["message"])
                except ValueError:
                    pass
        if resp["nextForwardToken"] == token:
            return
        token = resp["nextForwardToken"]


def app_summary(records):
    n, cmds, lat, started, chats = Counter(), Counter(), [], {}, []
    reasons = Counter()
    for ts, r in records:
        m, req = r.get("message", ""), r.get("requestId")
        if "agent invoked" in m:
            n["llamadas"] += 1
            started[req] = ts
        elif m.startswith("LLM returned"):
            n["modelo_ok"] += 1
        elif m.startswith("LLM parse failed"):
            n["respaldo_por_parseo"] += 1
        elif "overriding model" in m:
            n["filtro_corrigio"] += 1
            reasons[m.split("overriding model (", 1)[-1].split(")", 1)[0]] += 1
        elif "target fitted to pitch" in m:
            n["movimiento_ajustado"] += 1
        elif "recovered malformed JSON" in m:
            n["json_reparado"] += 1
        elif "agent error" in m or "fallback also failed" in m:
            n["errores"] += 1
        elif "teamChat changed" in m:
            chats.append((ts, m.split("teamChat changed: ", 1)[-1]))
        if req in started and (m.startswith("LLM returned") or m.startswith("Fallback returned")):
            lat.append((ts - started.pop(req)) / 1000)
    return n, lat, chats, reasons


def model_ticks(logs, group, start_ms, end_ms):
    """State the model saw and what it answered, one entry per call."""
    traces = defaultdict(lambda: {"states": [], "answer": None})
    kwargs = {"logGroupName": group, "logStreamNames": ["otel-rt-logs"], "startTime": start_ms, "endTime": end_ms}
    while True:
        resp = logs.filter_log_events(**kwargs)
        for e in resp["events"]:
            try:
                d = json.loads(e["message"])
            except ValueError:
                continue
            body = d.get("body") or {}
            t = traces[d.get("traceId")]
            if d.get("eventName") == "gen_ai.user.message":
                for c in body.get("content", []):
                    if isinstance(c, dict) and "Time:" in c.get("text", ""):
                        t["states"].append(c["text"])
            elif d.get("eventName") == "gen_ai.choice":
                for c in (body.get("message") or {}).get("content", []):
                    if isinstance(c, dict) and c.get("text"):
                        t["answer"] = c["text"]
        if "nextToken" not in resp:
            break
        kwargs["nextToken"] = resp["nextToken"]

    def game_time(s):
        m = re.search(r"Time: (\d+)s", s)
        return int(m.group(1)) if m else -1

    ticks = []
    for t in traces.values():
        if not t["states"] or not t["answer"]:
            continue
        state = max(t["states"], key=game_time)  # history resends older states
        cmd = re.search(r'"commandType"\s*:\s*"(\w+)"', t["answer"])
        ticks.append({
            "time": game_time(state),
            "score": (re.search(r"Score: (\d+-\d+)", state) or [None, "?"])[1],
            "has_ball": "hasBall=True" in state,
            "we_hold": "held by MY player" in state,
            "command": cmd.group(1) if cmd else "?",
            "positions": [(float(a), float(b)) for a, b in re.findall(r"\((-?[\d.]+),\s?(-?[\d.]+)\)", state)],
        })
    return sorted(ticks, key=lambda x: x["time"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--session", default="-team", help="substring of the match session id")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--json", help="also write the numbers to this file")
    args = ap.parse_args()

    logs = boto3.client("logs", region_name=args.region)
    runtimes = find_runtimes(args.region)
    if not runtimes:
        raise SystemExit("No ai_*_agent runtimes found in this account/region.")

    report, all_positions, timeline = {}, [], {}
    for pos in POSITIONS:
        if pos not in runtimes:
            print(f"{pos.upper():5} runtime no encontrado")
            continue
        group = f"/aws/bedrock-agentcore/runtimes/{runtimes[pos]}-DEFAULT"
        stream = match_stream(logs, group, args.session)
        if not stream:
            print(f"{pos.upper():5} sin partido que contenga {args.session!r}")
            continue
        records = list(read_stream(logs, group, stream))
        if not records:
            continue
        n, lat, chats, reasons = app_summary(records)
        start_ms, end_ms = records[0][0] - 5000, records[-1][0] + 5000
        ticks = model_ticks(logs, group, start_ms, end_ms)
        cmds = Counter(t["command"] for t in ticks)
        no_ball = sum(t["command"] in POSSESSION_COMMANDS and not t["has_ball"] for t in ticks)
        press_own = sum(t["command"] in ("PRESS_BALL", "INTERCEPT") and t["we_hold"] for t in ticks)
        for t in ticks:
            all_positions.extend(t["positions"])
            timeline[t["time"]] = t["score"]
        report[pos] = {
            "session": stream.split("[runtime-logs-", 1)[-1].split("]", 1)[0],
            "inicio_utc": datetime.fromtimestamp(records[0][0] / 1000, timezone.utc).strftime("%H:%M:%S"),
            **n,
            "latencia_p50_s": round(statistics.median(lat), 2) if lat else None,
            "latencia_max_s": round(max(lat), 2) if lat else None,
            "comandos_del_modelo": dict(cmds.most_common()),
            "con_balon": sum(t["has_ball"] for t in ticks),
            "pide_pase_tiro_saque_sin_balon": no_ball,
            "persigue_balon_propio": press_own,
            "mensajes_entrenador": [c for _, c in chats],
            "razones_del_filtro": dict(reasons.most_common()),
        }

    first = next(iter(report.values()), None)
    if first:
        print(f"Partido: sesion {first['session']}  inicio {first['inicio_utc']} UTC\n")
    print(f"{'pos':5} {'llam':>4} {'ok':>4} {'filtro':>6} {'ajuste':>6} {'resp':>4} {'err':>3} "
          f"{'p50':>5} {'conBalon':>8} {'sinBalon':>8} {'propio':>7}  comandos del modelo")
    for pos, r in report.items():
        print(f"{pos.upper():5} {r.get('llamadas',0):4} {r.get('modelo_ok',0):4} {r.get('filtro_corrigio',0):6} "
              f"{r.get('movimiento_ajustado',0):6} {r.get('respaldo_por_parseo',0):4} {r.get('errores',0):3} "
              f"{r['latencia_p50_s'] or 0:5.2f} {r['con_balon']:8} {r['pide_pase_tiro_saque_sin_balon']:8} "
              f"{r['persigue_balon_propio']:7}  {r['comandos_del_modelo']}")

    for pos, r in report.items():
        if r["razones_del_filtro"]:
            print(f"  filtro {pos.upper():5} {r['razones_del_filtro']}")

    if all_positions:
        xs = [p[0] for p in all_positions]
        ys = [p[1] for p in all_positions]
        print(f"\nCampo observado: x [{min(xs):.1f}, {max(xs):.1f}]  y [{min(ys):.1f}, {max(ys):.1f}]")
    changes, last = [], None
    for t in sorted(timeline):
        if timeline[t] != last:
            changes.append(f"{t}s {timeline[t]}")
            last = timeline[t]
    print("Marcador (local-visitante):", " -> ".join(changes) or "sin datos")
    chats = sorted({c for r in report.values() for c in r["mensajes_entrenador"]})
    print("Mensajes del entrenador vistos:", chats if chats else "ninguno")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nGuardado en {args.json}")


if __name__ == "__main__":
    main()
