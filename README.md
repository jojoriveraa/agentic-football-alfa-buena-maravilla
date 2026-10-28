# alfa buena maravilla — AWS Agentic Football Cup

Nuestro equipo de 5 agentes de IA para el taller [AWS Agentic Football Cup](https://catalog.workshops.aws/agentic-football/en-US). Cada jugador es un agente hecho con [Strands Agents](https://github.com/strands-agents/sdk-python) (librería de AWS para crear agentes), desplegado en Amazon Bedrock AgentCore Runtime (el servicio de AWS que ejecuta agentes). Cada uno usa un modelo Amazon Nova de Bedrock.

Parte del código de ejemplo de AWS (`aws-samples/sample-ai-possibilities`, carpeta `agentic-football-sample-agents`) e incluye nuestras correcciones.

**👉 Qué cambiamos y cómo nos fue: [.docs/bitacora.md](.docs/bitacora.md)**

## Qué tiene de distinto al ejemplo original

- **Escala real del campo.** El servidor del taller usa un campo unas 10 veces más chico que el que describe la guía. Los agentes la detectan en cada turno.
- **Filtros de comandos imposibles.** Nada de sacar, pasar o tirar sin balón, ni presionar a un compañero.
- **Instrucciones del entrenador.** Los agentes leen `teamChat`, el campo donde llegan los mensajes que mandas desde el portal.
- **Herramienta de análisis por partido:** [`tools/analizar_partido.py`](tools/analizar_partido.py).
- **Componentes de AgentCore** (Phase 3, la fase de mejoras del taller): un guardrail (filtro de Bedrock) contra los mensajes del coach rival que intentan manipular a nuestros agentes, Evaluations (un modelo juez califica las jugadas), Code Interpreter y Browser. Se crean con [`tools/crear_componentes.py`](tools/crear_componentes.py); el detalle está en la [bitácora, v6](.docs/bitacora.md#v6--componentes-de-agentcore-guardrail-evaluations-code-interpreter-y-browser).

## Estructura

```
lib/                          # Biblioteca compartida por todos los equipos
  state.py                    #   Resumen del estado para el modelo, escala del campo, teamChat
  guards.py                   #   Filtros de comandos imposibles
  fallback.py                 #   Lógica de respaldo por posición (cuando el modelo falla)
  agent_base.py               #   Crea el agente y maneja cada llamada
  coach_guard.py              #   Filtra los mensajes del coach con el guardrail
ai-team-strands-balanced/     # Nuestro equipo (5 agentes: ai-gk, ai-def, ai-mid, ai-fwd1, ai-fwd2)
ai-team-strands-*/            # Otros equipos de ejemplo de AWS (agresivo, defensivo, memoria, gateway)
tools/analizar_partido.py     # Reporte post-partido desde CloudWatch (el servicio de registros de AWS)
tools/analizar_rivales.py     # Origen de los goles y formas de juego de ambos equipos
tools/crear_componentes.py    # Crea guardrail, evaluador, Code Interpreter y Browser (idempotente)
tools/analisis_sandbox.py     # Tendencia de la temporada, calculada en el Code Interpreter
tools/leer_taller.py          # Lee páginas del taller con el Browser de AgentCore
.docs/                        # Bitácora de versiones y reportes de partidos
```

## Uso rápido (Linux o macOS)

Requisitos: Python 3.10 o más nuevo, AWS CLI, `zip`, `rsync` y [uv](https://docs.astral.sh/uv/) (gestor de paquetes de Python).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install strands-agents bedrock-agentcore-starter-toolkit
pip install -r ai-team-strands-balanced/ai-gk/requirements.txt
pip install playwright                                        # solo para tools/leer_taller.py

# Pruebas sin AWS
(cd lib && python3 test_state.py && python3 test_guards.py && python3 test_parsing.py)
python3 ai-team-strands-balanced/ai-gk/test_local.py

# Con las credenciales del taller (Workshop Studio → "Get AWS CLI credentials")
export AWS_DEFAULT_REGION=us-east-1
python3 ai-team-strands-balanced/ai-gk/test_local.py --llm   # prueba con el modelo real
python3 tools/crear_componentes.py                           # una vez por cuenta, antes del despliegue
cd ai-team-strands-balanced && ./deploy-all.sh                # despliega los 5 agentes
./deploy-all.sh ai-mid                                        # o solo uno
cd .. && python3 tools/analizar_partido.py                    # analiza el último partido
```

Después del despliegue, registra el ARN (el identificador del agente en AWS) de cada agente en **My Team** del [Player Portal](https://agentic-football.aws.dev). Para obtenerlos:

```bash
aws bedrock-agentcore-control list-agent-runtimes \
  --query 'agentRuntimes[].[agentRuntimeName,agentRuntimeArn]' --output table
```

## Antes de cada partido

- `PLAYER_NAME` de cada `ai-team-strands-balanced/<jugador>/src/main.py` debe coincidir con el nombre que tiene ese jugador en el Player Portal. Así sabe cuándo el entrenador le habla a él.
- Cada mensaje del entrenador lo escuchan los 10 jugadores, incluidos los rivales. Nombra al jugador y la acción, por ejemplo: `Rick, press the ball carrier now`.

## Nunca subas

Credenciales de AWS, el código de equipo del Player Portal ni los ARN o el número de cuenta del taller. Este repositorio es público.

## Licencia

MIT No Attribution, igual que el código original de AWS. Ver [LICENSE](LICENSE).
