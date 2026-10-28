"""Season trend of our players, computed inside the AgentCore Code Interpreter.

Uploads every match report in .docs/partidos/*.json (written by
analizar_partido.py --json) to the sandbox created by crear_componentes.py and
runs the pandas analysis there, so the numbers come from AWS, not a laptop.

Usage (repo root, workshop credentials active):

    python3 tools/analisis_sandbox.py
"""

import glob
import json
import os

from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RESOURCES = os.path.join(ROOT, "lib", "agentcore_resources.json")

ANALYSIS = r'''
import glob, json
import pandas as pd

rows = []
for path in sorted(glob.glob("partidos/*.json")):
    match = path.split("/")[-1].split("-")[3]  # 2026-10-28-p6-... -> p6
    for pos, a in json.load(open(path)).items():
        n = a.get("llamadas") or 0
        if not n:
            continue
        rows.append({
            "partido": match, "pos": pos,
            "filtro_%": round(100 * a.get("filtro_corrigio", 0) / n),
            "sin_balon_%": round(100 * a.get("pide_pase_tiro_saque_sin_balon", 0) / n),
            "con_balon": a.get("con_balon", 0),
            "p50_s": a.get("latencia_p50_s"),
        })
df = pd.DataFrame(rows)
pd.set_option("display.width", 140)
for col in ["filtro_%", "sin_balon_%", "con_balon", "p50_s"]:
    print(f"\n== {col} (fila = jugador, columna = partido)")
    print(df.pivot(index="pos", columns="partido", values=col).reindex(["gk", "def", "mid", "fwd1", "fwd2"]))
team = df.groupby("partido").agg({"filtro_%": "mean", "sin_balon_%": "mean", "con_balon": "sum", "p50_s": "median"}).round(2)
print("\n== equipo por partido")
print(team)
'''


def text_of(result) -> str:
    """Concatenate stdout/stderr from the executeCode event stream."""
    out = []
    for event in result.get("stream", []):
        for item in event.get("result", {}).get("content", []):
            if item.get("type") == "text":
                out.append(item.get("text", ""))
        sc = event.get("result", {}).get("structuredContent") or {}
        if sc.get("stderr"):
            out.append(sc["stderr"])
    return "\n".join(out)


def main():
    ci_id = json.load(open(RESOURCES)).get("code_interpreter_id")
    if not ci_id:
        raise SystemExit("Corre primero tools/crear_componentes.py (falta code_interpreter_id).")
    reports = sorted(glob.glob(os.path.join(ROOT, ".docs", "partidos", "*.json")))
    if not reports:
        raise SystemExit("No hay reportes .json en .docs/partidos/.")

    ci = CodeInterpreter(os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    ci.start(identifier=ci_id)
    try:
        for path in reports:
            ci.upload_file(f"partidos/{os.path.basename(path)}", open(path).read())
        print(text_of(ci.execute_code(ANALYSIS)))
    finally:
        ci.stop()


if __name__ == "__main__":
    main()
