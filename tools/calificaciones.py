"""Grades the AgentCore Evaluations judge gave our players, per match.

The online evaluations created by crear_componentes.py score a sample of each
agent's ticks some 15 minutes after the match ends (the session has to go
idle first). This reads their result log groups and prints, per position:
how many ticks were graded, the mean score (1 Good, 0.5 Questionable, 0 Bad),
the label counts, the built-in InstructionFollowing score, and the judge's
reason for a few Bad grades.

Usage (repo root, workshop credentials active):

    python3 tools/calificaciones.py                  # latest match
    python3 tools/calificaciones.py --session 995b1880
"""

import argparse
import json
import time
from collections import Counter, defaultdict

import boto3

PREFIX = "/aws/bedrock-agentcore/evaluations/results/afwc_partidos_"
POSITIONS = ["gk", "def", "mid", "fwd1", "fwd2"]


def results(logs, group, since_ms):
    kwargs = {"logGroupName": group, "startTime": since_ms}
    while True:
        resp = logs.filter_log_events(**kwargs)
        for e in resp.get("events", []):
            try:
                yield json.loads(e["message"])
            except ValueError:
                continue
        if not resp.get("nextToken"):
            break
        kwargs["nextToken"] = resp["nextToken"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--session", help="part of the match session id (default: latest graded match)")
    ap.add_argument("--hours", type=float, default=24, help="how far back to look")
    ap.add_argument("--bad", type=int, default=2, help="Bad reasons to show per position")
    args = ap.parse_args()

    logs = boto3.client("logs")
    since = int((time.time() - args.hours * 3600) * 1000)
    groups = {g["logGroupName"] for p in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=PREFIX)
              for g in p["logGroups"]}

    by_match = defaultdict(lambda: defaultdict(list))  # match -> pos -> records
    for group in sorted(groups):
        pos = group[len(PREFIX):].split("-")[0]
        for r in results(logs, group, since):
            a = {**r.get("attributes", {}), "_t": int(r.get("timeUnixNano", 0))}
            match = a.get("session.id", "?").rsplit("-team", 1)[0]
            by_match[match][pos].append(a)
    if not by_match:
        raise SystemExit("Sin calificaciones todavía (el juez califica ~15 min después del partido).")

    if args.session:
        match = next((m for m in by_match if args.session in m), None)
        if not match:
            raise SystemExit(f"Ningún partido calificado contiene {args.session!r}.")
    else:
        match = max(by_match, key=lambda m: max(a["_t"] for recs in by_match[m].values() for a in recs))
    print(f"Partido {match}\n")
    print(f"{'pos':5} {'jugadas':>7} {'promedio':>8}  {'Good':>4} {'Quest':>5} {'Bad':>4}  {'sigue prompt':>12}")
    bad_reasons = {}
    for pos in POSITIONS:
        recs = by_match[match].get(pos, [])
        mine = [a for a in recs if a.get("gen_ai.evaluation.name") == "afwc_decision_quality"]
        follow = [a for a in recs if a.get("gen_ai.evaluation.name") == "Builtin.InstructionFollowing"]
        if not mine:
            print(f"{pos.upper():5} {'pendiente':>7}")
            continue
        scores = [float(a["gen_ai.evaluation.score.value"]) for a in mine]
        labels = Counter(a.get("gen_ai.evaluation.score.label") for a in mine)
        fscores = [float(a["gen_ai.evaluation.score.value"]) for a in follow if "gen_ai.evaluation.score.value" in a]
        fmean = f"{sum(fscores) / len(fscores):.2f}" if fscores else "-"
        print(f"{pos.upper():5} {len(mine):7} {sum(scores) / len(scores):8.2f}  {labels['Good']:4} "
              f"{labels['Questionable']:5} {labels['Bad']:4}  {fmean:>12}")
        bad_reasons[pos] = [a.get("gen_ai.evaluation.explanation", "") for a in mine
                            if a.get("gen_ai.evaluation.score.label") == "Bad"][:args.bad]
    for pos, reasons in bad_reasons.items():
        for text in reasons:
            short = " ".join(text.split())[-300:]
            print(f"\n  Bad {pos.upper()}: ...{short}")


if __name__ == "__main__":
    main()
