"""Create (or find) the AgentCore building blocks the team uses and write
lib/agentcore_resources.json, which deploy-all.sh ships with lib/.

Usage (repo root, workshop credentials active):

    python3 tools/crear_componentes.py            # create what is missing
    python3 tools/crear_componentes.py --show     # only print what exists

Idempotent: every component is looked up by name first. The JSON holds
account-specific ids, so it is git-ignored; each account runs this once.
"""

import argparse
import io
import json
import os
import time
import zipfile

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "agentcore_resources.json")

GUARDRAIL_NAME = "afwc-coach-prompt-attack"
EVAL_ROLE_NAME = "AgentCore-afwc-evaluations-role"
EVALUATOR_NAME = "afwc_decision_quality"
ONLINE_EVAL_NAME = "afwc_partidos"
CODE_INTERPRETER_NAME = "afwc_analisis"
BROWSER_NAME = "afwc_navegador"
MEMORY_NAME = "afwc_lecciones"
GATEWAY_NAME = "afwc-coach-tools"
GATEWAY_ROLE_NAME = "AgentCore-afwc-gateway-role"
COACH_LAMBDA_NAME = "afwc-coach-tools"
COACH_LAMBDA_ROLE_NAME = "AgentCore-afwc-coach-lambda"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
JUDGE_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
AGENTS = ["ai_gk_agent", "ai_def_agent", "ai_mid_agent", "ai_fwd1_agent", "ai_fwd2_agent"]


def guardrail(bedrock, create: bool):
    """Prompt-attack filter for coach messages: every message reaches all 10
    players, so the opposing coach can try to talk to our agents."""
    for page in bedrock.get_paginator("list_guardrails").paginate():
        for g in page["guardrails"]:
            if g["name"] == GUARDRAIL_NAME:
                versions = [v["version"] for v in bedrock.list_guardrails(guardrailIdentifier=g["id"])["guardrails"]
                            if v["version"] != "DRAFT"]
                if versions:
                    return {"id": g["id"], "version": max(versions, key=int)}
                if create:
                    v = bedrock.create_guardrail_version(guardrailIdentifier=g["id"])["version"]
                    return {"id": g["id"], "version": v}
    if not create:
        return None
    g = bedrock.create_guardrail(
        name=GUARDRAIL_NAME,
        description="Filtra intentos de manipular a los agentes en los mensajes del entrenador (teamChat)",
        contentPolicyConfig={"filtersConfig": [
            {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"}]},
        blockedInputMessaging="blocked",
        blockedOutputsMessaging="blocked",
    )
    v = bedrock.create_guardrail_version(guardrailIdentifier=g["guardrailId"])["version"]
    return {"id": g["guardrailId"], "version": v}


EVALUATOR_INSTRUCTIONS = """You are judging one decision of an AI football (soccer) player in a 5-a-side match simulation. The context ends with the game state this player saw: the pitch bounds, every player's position, who holds the ball, hasBall for this player, shootRange, and tactical hints. The player's answer is the JSON list of commands it chose. Judge only the football decision, not the writing style.

A good decision:
1. Uses ball commands (PASS, SHOOT, DRIBBLE, GK_DISTRIBUTE, KICK) only when the state says this player has the ball (hasBall=True).
2. With the ball: shoots when the opponent goal is within shootRange; otherwise passes forward to an open teammate or carries the ball forward; never passes backward in its own defensive third.
3. Without the ball: the player the hints call closest to the ball goes for it; the others move to open space ahead of the ball, as in the supportSpot hint; the goalkeeper stays near its own goal.
4. MOVE_TO targets lie inside the pitch bounds given in the state, not in the bigger reference units.

Context: {context}
Player decision: {assistant_turn}"""

EVALUATOR_SCALE = {"numerical": [
    {"value": 1.0, "label": "Good", "definition": "The command follows all four rules and is the sensible football choice for this state."},
    {"value": 0.5, "label": "Questionable", "definition": "The command is legal but a clearly better option was available, or it breaks rule 3 or 4."},
    {"value": 0.0, "label": "Bad", "definition": "The command breaks rule 1 or 2: a ball command without the ball, a backward pass in its own third, or not shooting when in range."},
]}


def evaluation_role(iam, account: str, create: bool):
    """Role AgentCore Evaluations assumes to read our traces and call the
    judge model. Trust and permissions follow evaluations-prerequisites."""
    try:
        return iam.get_role(RoleName=EVAL_ROLE_NAME)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        if not create:
            return None
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {
            "StringEquals": {"aws:SourceAccount": account, "aws:ResourceAccount": account},
            "ArnLike": {"aws:SourceArn": [
                f"arn:aws:bedrock-agentcore:{REGION}:{account}:evaluator/*",
                f"arn:aws:bedrock-agentcore:{REGION}:{account}:online-evaluation-config/*",
            ]},
        },
    }]}
    perms = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StartQuery"],
         "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:{REGION}:{account}:log-group:/aws/bedrock-agentcore/evaluations/*"},
        {"Effect": "Allow", "Action": ["logs:DescribeIndexPolicies", "logs:PutIndexPolicy"],
         "Resource": [f"arn:aws:logs:{REGION}:{account}:log-group:aws/spans",
                      f"arn:aws:logs:{REGION}:{account}:log-group:aws/spans:*"]},
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": [f"arn:aws:bedrock:*::foundation-model/*",
                      f"arn:aws:bedrock:{REGION}:{account}:inference-profile/*"]},
    ]}
    arn = iam.create_role(
        RoleName=EVAL_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="AgentCore Evaluations - judges our players decisions during matches",
    )["Role"]["Arn"]
    iam.put_role_policy(RoleName=EVAL_ROLE_NAME, PolicyName="evaluations", PolicyDocument=json.dumps(perms))
    return arn


def evaluator(ctl, create: bool):
    """LLM-as-a-judge on each tick (TRACE): was the command good football?"""
    for page in ctl.get_paginator("list_evaluators").paginate():
        for e in page["evaluators"]:
            if e["evaluatorName"] == EVALUATOR_NAME:
                return e["evaluatorId"]
    if not create:
        return None
    return ctl.create_evaluator(
        evaluatorName=EVALUATOR_NAME,
        level="TRACE",
        description="Calidad futbolistica de cada decision de un jugador",
        evaluatorConfig={"llmAsAJudge": {
            "instructions": EVALUATOR_INSTRUCTIONS,
            "ratingScale": EVALUATOR_SCALE,
            "modelConfig": {"bedrockEvaluatorModelConfig": {
                "modelId": JUDGE_MODEL, "inferenceConfig": {"maxTokens": 1024, "temperature": 0.0}}},
        }},
    )["evaluatorId"]


def runtime_log_groups(ctl):
    """Log group of each of our 5 runtimes, by agent name."""
    groups = {}
    for page in ctl.get_paginator("list_agent_runtimes").paginate():
        for r in page["agentRuntimes"]:
            if r["agentRuntimeName"] in AGENTS:
                groups[r["agentRuntimeName"]] = f"/aws/bedrock-agentcore/runtimes/{r['agentRuntimeId']}-DEFAULT"
    return groups


def online_evaluations(ctl, create: bool, role_arn, evaluator_id, sampling: float):
    """Judge a sample of live ticks, plus the built-in InstructionFollowing
    check (does the model obey its system prompt). The service takes one
    service name per config, so each agent gets its own afwc_partidos_<pos>."""
    existing = {}
    for page in ctl.get_paginator("list_online_evaluation_configs").paginate():
        for c in page["onlineEvaluationConfigs"]:
            existing[c["onlineEvaluationConfigName"]] = c["onlineEvaluationConfigId"]
    configs = {}
    for agent, group in runtime_log_groups(ctl).items():
        name = f"{ONLINE_EVAL_NAME}_{agent.split('_')[1]}"
        if name in existing:
            configs[agent] = existing[name]
            continue
        if not create or not role_arn or not evaluator_id:
            continue
        configs[agent] = ctl.create_online_evaluation_config(
            onlineEvaluationConfigName=name,
            description=f"Muestra de jugadas de {agent} durante los partidos",
            rule={"samplingConfig": {"samplingPercentage": sampling}},
            dataSourceConfig={"cloudWatchLogs": {"logGroupNames": [group], "serviceNames": [f"{agent}.DEFAULT"]}},
            evaluators=[{"evaluatorId": evaluator_id}, {"evaluatorId": "Builtin.InstructionFollowing"}],
            evaluationExecutionRoleArn=role_arn,
            enableOnCreate=True,
        )["onlineEvaluationConfigId"]
    return configs


def code_interpreter(ctl, create: bool):
    """Sandbox with no network where tools/analisis_sandbox.py crunches
    match reports, so the analysis code runs in AWS, not on a laptop."""
    for page in ctl.get_paginator("list_code_interpreters").paginate():
        for c in page["codeInterpreterSummaries"]:
            if c["name"] == CODE_INTERPRETER_NAME:
                return c["codeInterpreterId"]
    if not create:
        return None
    return ctl.create_code_interpreter(
        name=CODE_INTERPRETER_NAME,
        description="Analisis de partidos en sandbox sin red",
        networkConfiguration={"networkMode": "SANDBOX"},
    )["codeInterpreterId"]


def browser(ctl, create: bool):
    """Cloud browser that tools/leer_taller.py uses to read the workshop
    pages, which are a single-page app plain HTTP fetches cannot render."""
    for page in ctl.get_paginator("list_browsers").paginate():
        for b in page["browserSummaries"]:
            if b["name"] == BROWSER_NAME:
                return b["browserId"]
    if not create:
        return None
    return ctl.create_browser(
        name=BROWSER_NAME,
        description="Lee las paginas del taller con un navegador real",
        networkConfiguration={"networkMode": "PUBLIC"},
    )["browserId"]


def memory(ctl, create: bool):
    """Short-term memory (raw events, no extraction strategies) holding the
    lessons tools/lecciones.py saves and the goals the keeper records."""
    for page in ctl.get_paginator("list_memories").paginate():
        for m in page["memories"]:
            if m["id"].startswith(MEMORY_NAME + "-"):
                return {"id": m["id"], "arn": m["arn"]}
    if not create:
        return None
    m = ctl.create_memory(
        name=MEMORY_NAME,
        description="Lecciones entre partidos y goles registrados por el portero",
        eventExpiryDuration=365,
    )["memory"]
    return {"id": m["id"], "arn": m["arn"]}


def grant_memory(iam, ctl, memory_arn: str):
    """Let each of our runtimes read lessons and write goal events."""
    policy = json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents", "bedrock-agentcore:GetEvent"],
        "Resource": memory_arn,
    }]})
    for page in ctl.get_paginator("list_agent_runtimes").paginate():
        for r in page["agentRuntimes"]:
            if r["agentRuntimeName"] in AGENTS:
                role = ctl.get_agent_runtime(agentRuntimeId=r["agentRuntimeId"])["roleArn"].split("/")[-1]
                iam.put_role_policy(RoleName=role, PolicyName="afwc-memory", PolicyDocument=policy)


COACH_TOOLS = [
    {"name": "listar_lecciones", "description": "List the lessons every agent reads at kickoff, with id and position.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "guardar_leccion",
     "description": "Save a lesson for the next matches: one concrete instruction in English, up to 300 characters.",
     "inputSchema": {"type": "object", "properties": {
         "texto": {"type": "string", "description": "The instruction, as the model will read it"},
         "pos": {"type": "string", "description": "all, gk, def, mid, fwd1 or fwd2 (default all)"}},
         "required": ["texto"]}},
    {"name": "borrar_leccion", "description": "Delete a lesson by its id.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "listar_goles", "description": "Goals the keeper recorded: game time, score, ball and player positions.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _role(iam, name: str, service: str, account: str, policy: dict, description: str) -> str:
    """Create the role if missing and (re)write its inline policy."""
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        trust = {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account}}}]}
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                              Description=description)["Role"]["Arn"]
    iam.put_role_policy(RoleName=name, PolicyName="afwc", PolicyDocument=json.dumps(policy))
    return arn


def coach_lambda(iam, lam, account: str, memory: dict) -> str:
    """Lambda with the coach tools, packaged with lib/team_memory.py."""
    role = _role(iam, COACH_LAMBDA_ROLE_NAME, "lambda.amazonaws.com", account, {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:{REGION}:{account}:log-group:/aws/lambda/{COACH_LAMBDA_NAME}:*"},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents",
                                       "bedrock-agentcore:GetEvent", "bedrock-agentcore:DeleteEvent"],
         "Resource": memory["arn"]},
    ]}, "Coach tools Lambda - reads and writes the team lessons in AgentCore Memory")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(os.path.join(ROOT, "tools", "gateway_coach", "lambda_function.py"), "lambda_function.py")
        z.write(os.path.join(ROOT, "lib", "team_memory.py"), "team_memory.py")
    code = buf.getvalue()
    env = {"Variables": {"AFWC_MEMORY_ID": memory["id"]}}
    try:
        arn = lam.get_function(FunctionName=COACH_LAMBDA_NAME)["Configuration"]["FunctionArn"]
        lam.update_function_code(FunctionName=COACH_LAMBDA_NAME, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=COACH_LAMBDA_NAME)
        lam.update_function_configuration(FunctionName=COACH_LAMBDA_NAME, Environment=env)
        return arn
    except lam.exceptions.ResourceNotFoundException:
        pass
    for attempt in range(10):  # a new role takes a few seconds to be assumable
        try:
            return lam.create_function(
                FunctionName=COACH_LAMBDA_NAME, Runtime="python3.13", Role=role,
                Handler="lambda_function.lambda_handler", Code={"ZipFile": code},
                Timeout=15, MemorySize=256, Environment=env,
                Description="Herramientas del coach detras del AgentCore Gateway",
            )["FunctionArn"]
        except lam.exceptions.InvalidParameterValueException:
            if attempt == 9:
                raise
            time.sleep(5)


def gateway(iam, ctl, account: str, lambda_arn: str):
    """MCP gateway with IAM auth (only principals of this account can call
    it, since the tools write into the agents' prompts) and one Lambda
    target with the coach tools."""
    role = _role(iam, GATEWAY_ROLE_NAME, "bedrock-agentcore.amazonaws.com", account, {
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": lambda_arn}]},
        "AgentCore Gateway - invokes the coach tools Lambda")
    gw = next((g for page in ctl.get_paginator("list_gateways").paginate()
               for g in page["items"] if g["name"] == GATEWAY_NAME), None)
    if gw:
        gw_id = gw["gatewayId"]
    else:
        for attempt in range(10):
            try:
                gw_id = ctl.create_gateway(
                    name=GATEWAY_NAME, roleArn=role, protocolType="MCP", authorizerType="AWS_IAM",
                    description="Herramientas del coach: lecciones y goles en AgentCore Memory",
                )["gatewayId"]
                break
            except ctl.exceptions.ValidationException:
                if attempt == 9:
                    raise
                time.sleep(5)
    while ctl.get_gateway(gatewayIdentifier=gw_id)["status"] == "CREATING":
        time.sleep(3)
    targets = {t["name"] for t in ctl.list_gateway_targets(gatewayIdentifier=gw_id)["items"]}
    for attempt in range(10 if "coach" not in targets else 0):
        try:  # the new gateway role also takes a few seconds to be assumable
            ctl.create_gateway_target(
                gatewayIdentifier=gw_id, name="coach", description="Lecciones y goles del equipo",
                targetConfiguration={"mcp": {"lambda": {"lambdaArn": lambda_arn,
                                                        "toolSchema": {"inlinePayload": COACH_TOOLS}}}},
                credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            )
            break
        except ctl.exceptions.ValidationException:
            if attempt == 9:
                raise
            time.sleep(6)
    return {"id": gw_id, "url": f"https://{gw_id}.gateway.bedrock-agentcore.{REGION}.amazonaws.com/mcp"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--show", action="store_true", help="do not create anything")
    ap.add_argument("--sampling", type=float, default=20.0, help="percent of ticks the judge scores")
    args = ap.parse_args()
    create = not args.show

    resources = json.load(open(OUT)) if os.path.exists(OUT) else {}
    bedrock = boto3.client("bedrock", region_name=REGION)

    g = guardrail(bedrock, create)
    if g:
        resources["coach_guardrail"] = g

    account = boto3.client("sts").get_caller_identity()["Account"]
    ctl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    role = evaluation_role(boto3.client("iam"), account, create)
    ev = evaluator(ctl, create)
    online = online_evaluations(ctl, create, role, ev, args.sampling)
    if ev:
        resources["evaluations"] = {"evaluator_id": ev, "online_configs": online}
    ci = code_interpreter(ctl, create)
    if ci:
        resources["code_interpreter_id"] = ci
    br = browser(ctl, create)
    if br:
        resources["browser_id"] = br
    mem = memory(ctl, create)
    if mem:
        resources["memory_id"] = mem["id"]
        if create:
            grant_memory(boto3.client("iam"), ctl, mem["arn"])
            iam = boto3.client("iam")
            fn = coach_lambda(iam, boto3.client("lambda", region_name=REGION), account, mem)
            resources["coach_gateway"] = gateway(iam, ctl, account, fn)

    print(json.dumps(resources, indent=2))
    if create:
        with open(OUT, "w") as f:
            json.dump(resources, f, indent=2)
        print(f"\nEscrito {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
