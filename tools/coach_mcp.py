"""Call the coach tools on the AgentCore Gateway over MCP, signed with your AWS credentials.

The Gateway uses IAM auth, so every request is signed with SigV4 (AWS's
request signature). The same tools work from any MCP client that can sign,
for example Kiro through mcp-proxy-for-aws.

Usage (repo root, workshop credentials active):

    python3 tools/coach_mcp.py                         # list the tools
    python3 tools/coach_mcp.py listar_lecciones
    python3 tools/coach_mcp.py guardar_leccion '{"texto": "...", "pos": "mid"}'
    python3 tools/coach_mcp.py listar_goles
"""

import json
import os
import sys
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RESOURCES = os.path.join(ROOT, "lib", "agentcore_resources.json")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def rpc(url: str, method: str, params: dict | None = None, rid: int = 1) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    req = AWSRequest(method="POST", url=url, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    SigV4Auth(boto3.Session().get_credentials(), "bedrock-agentcore", REGION).add_auth(req)
    with urllib.request.urlopen(urllib.request.Request(url, data=body.encode(), headers=dict(req.headers))) as r:
        text = r.read().decode()
    if text.startswith("event:") or text.startswith("data:"):  # streamable HTTP answer
        text = next(l[5:] for l in text.splitlines() if l.startswith("data:"))
    reply = json.loads(text)
    if "error" in reply:
        raise SystemExit(f"Error del gateway: {reply['error']}")
    return reply["result"]


def main():
    gw = json.load(open(RESOURCES)).get("coach_gateway")
    if not gw:
        raise SystemExit("Corre primero tools/crear_componentes.py (falta coach_gateway).")
    url = gw["url"]
    rpc(url, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "afwc-coach", "version": "1"}})
    if len(sys.argv) < 2:
        for t in rpc(url, "tools/list", rid=2)["tools"]:
            print(f"{t['name']:40} {t.get('description', '')}")
        return
    name = sys.argv[1]
    if "___" not in name:
        name = f"coach___{name}"
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    result = rpc(url, "tools/call", {"name": name, "arguments": args}, rid=3)
    for item in result.get("content", []):
        print(item.get("text", item))


if __name__ == "__main__":
    main()
