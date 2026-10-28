# Bitácora del equipo "alfa buena maravilla"

Registro de cada versión de los agentes: qué cambió, cómo nos fue y qué sigue.
Cómo llenarla: ver [README de .docs](README.md).

## Resumen

| Versión | Fecha | Cambio principal | Partidos | G-E-P | Goles a favor / en contra |
|---|---|---|---|---|---|
| [v0](#v0--código-original-de-aws) | 2026-10-28 | Código de ejemplo de AWS, sin cambios | 2 | 0-0-2 | 3 / 7 |
| [v1](#v1--escala-del-campo-filtros-y-entrenador) | 2026-10-28 | Escala real del campo, filtros de comandos imposibles, instrucciones del entrenador | 0 | – | – |

G-E-P = ganados, empatados, perdidos.

## Partidos

| # | Fecha | Versión | Rival | Marcador | Posesión* | Tiros (a puerta)* | Reporte |
|---|---|---|---|---|---|---|---|
| 1 | 2026-10-28 | v0 | The Benchmark FC (equilibrado) | 1-3 | 41% | 4 (1) | [txt](partidos/2026-10-28-p1-benchmark-fc.txt) · [json](partidos/2026-10-28-p1-benchmark-fc.json) |
| 2 | 2026-10-28 | v0 | Total Attack United (muy ofensivo) | 2-4 | 34% | 2 (2) | [txt](partidos/2026-10-28-p2-total-attack-united.txt) · [json](partidos/2026-10-28-p2-total-attack-united.json) |

\* Según el Match Report del Player Portal. Ver la [nota sobre el reporte](#nota-el-match-report-invierte-las-columnas-de-comandos).

---

## v0 — Código original de AWS

**Commit:** `7074c00`, copia sin cambios de `aws-samples/sample-ai-possibilities@02fadb4`, equipo `ai-team-strands-balanced`.

**Resultado:** 2 derrotas, 3 goles a favor y 7 en contra. En lo técnico, cero errores: los 5 agentes respondieron en todos los turnos, con latencia típica de 0.6 a 1.1 s por turno.

### Qué se vio en los registros

Números de [`tools/analizar_partido.py`](../tools/analizar_partido.py), partido 1 / partido 2:

| Problema | Partido 1 | Partido 2 | Por qué importa |
|---|---|---|---|
| El campo real mide aprox. x ±7, y ±3.5; el código asume x ±55, y ±35 | sí | sí | Arcos, distancias y umbrales 10 veces más grandes que la realidad |
| GK pide `GK_DISTRIBUTE` (sacar) sin tener el balón | 60 de 88 turnos | 67 de 90 | El servidor lo ignora y el portero se queda quieto |
| FWD2 pide `SHOOT` (tirar) sin balón | 15 | 22 | Turnos perdidos |
| DEF y FWD2 presionan con el balón en su propio equipo | 28 + 27 | 26 + 0 | Corren hacia su compañero y vacían la defensa |
| FWD1 casi no tira | 4 tiros en 85 turnos | 5 en 85 | Cree estar a 57 unidades del arco cuando está a unas 7 |
| Nova Pro (MID) escribe `True` estilo Python | 55 de 88 | – | El código lo repara; solo cuesta claridad |
| Mensajes del entrenador (`teamChat`) | ninguno | ninguno | El código de ejemplo no los leía |

Goles en contra tempranos en el partido 2: 0-1 a los 16 s y 0-2 a los 23 s.

### Qué mejorar → v1

Lo operativo primero: sin una escala correcta, ninguna estrategia se puede evaluar.

---

## v1 — Escala del campo, filtros y entrenador

**Commits:** `84f1289` y el commit de la bitácora, que suma el filtro de "no perseguir el balón propio".
**Desplegado en AgentCore:** versión 3 de los runtimes, 2026-10-28.

### Cambios

- **Escala del campo** (`lib/state.py`): `pitch_scale()` estima el tamaño real a partir de los porteros. Cada turno informa `Pitch:`, los arcos y `shootRange`, el alcance de tiro en la escala real.
- **Lógica de respaldo** (`lib/fallback.py`): sus distancias se escalan al campo real.
- **Filtros** (`lib/guards.py`): `PASS`, `SHOOT` y `GK_DISTRIBUTE` sin balón, o `PRESS_BALL` e `INTERCEPT` con el balón en nuestro equipo, se cambian por la lógica de respaldo. Un `MOVE_TO` fuera del campo se reescala o se recorta.
- **Historial** (`lib/agent_base.py`): cada modelo ve 2 mensajes previos en vez de 40, con menos latencia y menos copia de sus propias respuestas.
- **Entrenador**: se leen los últimos 3 mensajes de `teamChat` y cada jugador tiene `PLAYER_NAME`. Por ahora "Chuy GK" (portero) y "Rick" (medio); faltan DEF, FWD1 y FWD2.
- **Instrucciones** de los 5 agentes: sin coordenadas fijas, `shootRange` en vez de "~25 unidades", y aclaran que pasar, tirar y sacar requieren `hasBall=True`.

### Qué esperamos ver

| Métrica del reporte | v0 | Esperado en v1 |
|---|---|---|
| `sinBalon` (pide pase, tiro o saque sin balón) | 60-67 (GK) | Igual o parecido. El filtro los corrige, así que `filtro` debe subir en la misma cantidad. |
| `propio` (persigue el balón propio) | 26-28 (DEF) | Corregido por el filtro |
| Tiros de FWD1 | 4-5 | Más, cuando `distOppGoal <= shootRange` |
| Latencia p50 de MID | 1.09 s | Menor, por el historial corto |
| Goles en contra | 3-4 por partido | Menos |

### Resultados

_Pendiente: jugar 2 o 3 partidos contra The Benchmark FC para comparar con v0._

### Qué mejorar → v2

_Llenar después de los partidos de v1._

---

## Pendientes de estrategia

Ordenados por impacto esperado según los datos de v0. Regla: **un cambio por versión**, con 2 o 3 partidos contra el mismo rival antes de concluir, porque los modelos no responden igual cada vez.

1. **Modelo del portero.** Nova Micro sigue pidiendo `GK_DISTRIBUTE` sin balón incluso en campo chico (prueba local de v1). Probar Nova Lite en el GK. Se mide con la columna `filtro` del GK, que debería bajar.
2. **Defensa detrás del balón.** Los goles tempranos llegan con el DEF adelantado. Instrucción explícita: nunca pasar la mitad del campo propia y mantenerse entre el balón y el arco propio.
3. **Delanteros que tiran.** Si en v1 FWD1 sigue sin tirar, agregar la regla "si `hasBall=True` y `distOppGoal <= shootRange`, tira en este turno", o subir `shootRange`.
4. **Formación.** El portal tiene `2-1-1`; los agentes juegan `1-1-2`. Alinear para que los saques de centro arranquen bien.
5. **Plan de entrenador.** Completar los 5 nombres, usar las frases preparadas y confirmar en los registros el formato real de `teamChat`.
6. **Por rival.** Contra Total Attack United, contragolpe: delanteros arriba esperando el balón. Contra Fort Knox Athletic, tiros desde media distancia.
7. **Phase 3** (la fase de mejoras del taller): Memory, Gateway, Observability y Evaluations de AgentCore. Además, el portal da trofeos por usarlas (vamos 4 de 10).

## Nota: el Match Report invierte las columnas de comandos

En el partido 1, los conteos que el reporte atribuye a "The Benchmark FC" coinciden exactamente con lo que mandaron nuestros agentes según sus registros: 115 `PRESS_BALL`, 64 `GK_DISTRIBUTE`, 35 `PASS`, 23 `SHOOT`, 10 `INTERCEPT` y 1 `MARK`. Además, el consejo del entrenador para el rival menciona a "FWD 4", que es nuestro jugador. Pasa lo mismo en el partido 2: 71 `GK_DISTRIBUTE` aparecen del lado del rival. Los goles sí están bien atribuidos.

Conclusión: para nuestros comandos, usar `tools/analizar_partido.py` y no la sección "How agents played" del reporte.
