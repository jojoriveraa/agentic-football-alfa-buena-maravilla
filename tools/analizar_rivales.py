"""Reconstruct our matches from the states our 5 agents saw, focusing on opponents.

Usage (repo root, workshop credentials active):

    python3 tools/analizar_rivales.py 8024de02:"P4 Benchmark" 6b8b646e:"P3 Fort Knox"

Each argument is part of a match session id (first line of
analizar_partido.py's report) and a label. Read-only. For each match: goal origins (last holder before each goal, its
distance to the goal), possession share by samples, and each team's average
shape with and without the ball.
"""

import json
import math
import re
import sys
from collections import defaultdict

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import boto3  # noqa: E402
from analizar_partido import find_runtimes, match_stream, read_stream  # noqa: E402

GOAL_X = 6.6
logs = boto3.client("logs", region_name="us-east-1")
runtimes = find_runtimes("us-east-1")


def window(session):
    for pos, rid in runtimes.items():
        g = f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT"
        st = match_stream(logs, g, session)
        if st:
            recs = list(read_stream(logs, g, st))
            if recs:
                return recs[0][0] - 3000, recs[-1][0] + 3000
    return None


def states(start, end):
    """Unique states across the 5 agents: time -> parsed snapshot."""
    snaps = {}
    for label, rid in runtimes.items():
        g = f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT"
        kw = {"logGroupName": g, "logStreamNames": ["otel-rt-logs"], "startTime": start,
              "endTime": end, "filterPattern": '"gen_ai.user.message"'}
        while True:
            r = logs.filter_log_events(**kw)
            for e in r["events"]:
                try:
                    d = json.loads(e["message"])
                except ValueError:
                    continue
                for c in (d.get("body") or {}).get("content", []):
                    t = c.get("text", "") if isinstance(c, dict) else ""
                    m = re.search(r"Time: (\d+)s \| Score: (\d+)-(\d+) \| Team: (\d)", t)
                    if not m:
                        continue
                    key = int(m.group(1))
                    if key in snaps:
                        continue
                    team = int(m.group(4))
                    ball = re.search(r"Ball: \((-?[\d.]+), (-?[\d.]+)\) held by ([^\n]+)", t)
                    me = re.search(r">>> YOUR PLAYER \((\w+)[^,]*, id=(\d)\): pos=\((-?[\d.]+),(-?[\d.]+)\)", t)
                    mates = {int(i): (float(x), float(y)) for i, x, y in
                             re.findall(r"\(id=(\d)\): \((-?[\d.]+),(-?[\d.]+)\)", t.split("Opponents")[0])}
                    if me:
                        mates[int(me.group(2))] = (float(me.group(3)), float(me.group(4)))
                    opps = {int(i): (float(x), float(y)) for i, x, y in
                            re.findall(r"  P(\d): \((-?[\d.]+),(-?[\d.]+)\)", t.split("Opponents", 1)[-1])}
                    snaps[key] = {
                        "time": int(m.group(1)), "ts": e["timestamp"], "home": int(m.group(2)),
                        "away": int(m.group(3)), "team": team,
                        "ball": (float(ball.group(1)), float(ball.group(2))) if ball else None,
                        "holder": ball.group(3).strip() if ball else "?",
                        "mates": mates, "opps": opps,
                    }
            if "nextToken" not in r:
                break
            kw["nextToken"] = r["nextToken"]
    out = [snaps[k] for k in sorted(snaps)]
    # Re-attribute possession: agentId_N exists once per team and the agents
    # resolve it to whichever player is listed first. Take the closer one.
    for s in out:
        h = s["holder"]
        if (h.startswith("MY") or h.startswith("OPP")) and s["ball"]:
            idx = int(h.split()[-1])
            m, o = s["mates"].get(idx), s["opps"].get(idx)
            if m and o:
                s["seen_as"] = h
                s["holder"] = ("MY" if math.dist(m, s["ball"]) <= math.dist(o, s["ball"]) else "OPP") + f" player {idx}"
    return out


def analyse(session, label):
    w = window(session)
    if not w:
        print(f"## {label}: sin datos")
        return
    S = states(*w)
    team = S[0]["team"]
    a = 1 if team == 0 else -1            # our attacking direction
    our_goal, their_goal = -a * GOAL_X, a * GOAL_X
    print(f"\n## {label}  ({len(S)} muestras, jugamos {'HOME' if team == 0 else 'AWAY'})")

    # Possession share by samples
    pos = defaultdict(int)
    for s in S:
        h = s["holder"]
        pos["nuestro" if h.startswith("MY") else "rival" if h.startswith("OPP") else "libre"] += 1
    tot = sum(pos.values())
    print("  posesion por muestras (corregida): " + ", ".join(f"{k} {100*v/tot:.0f}%" for k, v in pos.items()))
    fooled = sum(1 for s in S if s.get("seen_as", "").startswith("MY") and s["holder"].startswith("OPP"))
    print(f"  muestras donde los agentes creian tener el balon y lo tenia el rival: {fooled} de {tot}")
    last_ball = None

    # Goals: last holder before each score change
    prev = None
    for i, s in enumerate(S):
        score = (s["home"], s["away"])
        if prev and score != prev:
            scorer_is_home = score[0] > prev[0]
            ours = (scorer_is_home and team == 0) or (not scorer_is_home and team == 1)
            target = their_goal if ours else our_goal
            before = [x for x in S[max(0, i - 6):i] if x["holder"].startswith("MY" if ours else "OPP")]
            if before:
                b = before[-1]
                idx = int(b["holder"].split()[-1])
                xy = (b["mates"] if ours else b["opps"]).get(idx)
                d = math.dist(xy, (target, 0)) if xy else None
                gap = (s["ts"] - b["ts"]) / 1000
                role = ["GK", "DEF", "MID", "FWD1", "FWD2"][idx] if ours else f"rival P{idx}"
                print(f"  gol {'NUESTRO' if ours else 'en contra'} {s['time']:3}s: ultimo con balon {role} "
                      f"en {xy} a {d:.1f} del arco ({d / (2 * GOAL_X) * 100:.0f}% del largo), "
                      f"{gap:.1f}s antes del gol" if d is not None else
                      f"  gol {'NUESTRO' if ours else 'en contra'} {s['time']:3}s: sin posicion del ultimo con balon")
            else:
                print(f"  gol {'NUESTRO' if ours else 'en contra'} {s['time']:3}s: no vi quien lo tenia antes")
        prev = score

    # Shapes: average x (in our attacking frame) and spread, with/without the ball
    for who, key in (("rival", "opps"), ("nosotros", "mates")):
        for phase in ("con balon", "sin balon"):
            sel = [s for s in S if (s["holder"].startswith("OPP" if who == "rival" else "MY")) == (phase == "con balon")]
            if not sel:
                continue
            avg = {}
            for idx in range(5):
                xs = [a * s[key][idx][0] for s in sel if idx in s[key]]
                if xs:
                    avg[idx] = sum(xs) / len(xs)
            shape = " ".join(f"P{k}:{v:+.1f}" for k, v in sorted(avg.items()))
            width = [abs(s[key][i][1]) for s in sel for i in s[key] if i]
            print(f"  forma {who:8} {phase}: x medio (+ = hacia el arco rival) {shape} | ancho medio {sum(width)/len(width):.1f}")


for arg in sys.argv[1:]:
    sess, label = arg.split(":", 1)
    analyse(sess, label)
