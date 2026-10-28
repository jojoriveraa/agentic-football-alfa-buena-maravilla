"""Create (or find) the AgentCore building blocks the team uses and write
lib/agentcore_resources.json, which deploy-all.sh ships with lib/.

Usage (repo root, workshop credentials active):

    python3 tools/crear_componentes.py            # create what is missing
    python3 tools/crear_componentes.py --show     # only print what exists

Idempotent: every component is looked up by name first. The JSON holds
account-specific ids, so it is git-ignored; each account runs this once.
"""

import argparse
import json
import os

import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "agentcore_resources.json")

GUARDRAIL_NAME = "afwc-coach-prompt-attack"
EVAL_ROLE_NAME = "AgentCore-afwc-evaluations-role"
EVALUATOR_NAME = "afwc_decision_quality"
ONLINE_EVAL_NAME = "afwc_partidos"
CODE_INTERPRETER_NAME = "afwc_analisis"
BROWSER_NAME = "afwc_navegador"
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

    print(json.dumps(resources, indent=2))
    if create:
        with open(OUT, "w") as f:
            json.dump(resources, f, indent=2)
        print(f"\nEscrito {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
