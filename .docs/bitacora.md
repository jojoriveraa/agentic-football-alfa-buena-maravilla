# Bitácora del equipo "alfa buena maravilla"

Registro de cada versión de los agentes: qué cambió, cómo nos fue y qué sigue.
Cómo llenarla: ver [README de .docs](README.md).

## Resumen

| Versión | Fecha | Cambio principal | Partidos | G-E-P | Goles a favor / en contra |
|---|---|---|---|---|---|
| [v0](#v0--código-original-de-aws) | 2026-10-28 | Código de ejemplo de AWS, sin cambios | 2 | 0-0-2 | 3 / 7 |
| [v1](#v1--escala-del-campo-filtros-y-entrenador) | 2026-10-28 | Escala real del campo, filtros de comandos imposibles, instrucciones del entrenador | 1 | 0-0-1 | 0 / 1 |
| [v2](#v2--juego-de-posesión-triangulaciones-autopase-y-despeje-largo) | 2026-10-28 | Triangulaciones, sin pases atrás en zona propia, autopase con presión, despeje largo del portero | 1 | 0-0-1 | 0 / 1 |
| [v3](#v3--tiros-de-media-distancia-portero-en-su-arco-y-sin-retroceder-en-ataque) | 2026-10-28 | Tiros de media distancia, portero atado a su arco, nada de retroceder en ataque | 0 | – | – |

G-E-P = ganados, empatados, perdidos.

## Partidos

| # | Fecha | Versión | Rival | Marcador | Posesión* | Tiros (a puerta)* | Reporte |
|---|---|---|---|---|---|---|---|
| 1 | 2026-10-28 | v0 | The Benchmark FC (equilibrado) | 1-3 | 41% | 4 (1) | [txt](partidos/2026-10-28-p1-benchmark-fc.txt) · [json](partidos/2026-10-28-p1-benchmark-fc.json) |
| 2 | 2026-10-28 | v0 | Total Attack United (muy ofensivo) | 2-4 | 34% | 2 (2) | [txt](partidos/2026-10-28-p2-total-attack-united.txt) · [json](partidos/2026-10-28-p2-total-attack-united.json) |
| 3 | 2026-10-28 | v1 | Fort Knox Athletic (muy defensivo) | 0-1 | 45% | 1 (0) | [txt](partidos/2026-10-28-p3-fort-knox-athletic.txt) · [json](partidos/2026-10-28-p3-fort-knox-athletic.json) |
| 4 | 2026-10-28 | v2 | _por confirmar_ | 0-1 (gol a los 182 s) | – | – | [txt](partidos/2026-10-28-p4-rival-por-confirmar.txt) · [json](partidos/2026-10-28-p4-rival-por-confirmar.json) |

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

**Commits:** `84f1289` y `2579865`, que suma el filtro de "no perseguir el balón propio".
**Desplegado en AgentCore:** versión 3 de los runtimes, 2026-10-28 18:21 UTC.

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

Un partido, el 3, perdido 0-1 contra Fort Knox Athletic (muy defensivo). No es comparable directo con v0: el rival es otro y es un solo partido.

| Métrica | v0 (partidos 1 y 2) | v1 (partido 3) |
|---|---|---|
| Goles en contra | 3 y 4 | **1** |
| Goles a favor | 1 y 2 | 0 |
| Decisiones corregidas por el filtro | – | **52**: GK 19 sacar sin balón; DEF 22 presionar balón propio y 2 pasar sin balón; MID 4 y FWD2 4 presionar balón propio; FWD2 1 tirar sin balón |
| GK pide sacar sin balón | 60 y 67 (~70% de sus turnos) | 19 (30%). Con el historial corto el modelo copia menos su respuesta anterior. |
| Latencia p50 de GK / DEF / MID / FWD1 / FWD2 | 0.64 / 0.79 / 1.09 / 0.64 / 0.94 s | **0.48 / 0.58 / 0.69 / 0.53 / 0.65 s** |
| Tiros pedidos por FWD1 / FWD2 | 4-5 / 19-29 (muchos sin balón) | 7 / 11 |
| Mensajes del entrenador | ninguno | ninguno: no se probó |

Lectura: lo operativo funciona. Los filtros actúan, la latencia bajó entre 25% y 37% y recibimos menos goles. Falta generar peligro: contra un rival encerrado atrás no hubo goles.

### Qué mejorar → v2

Pedidos del entrenador después del partido 3, más lo que muestran los datos de los 3 partidos:

- **Triangular con el balón.** Los compañeros del que lleva el balón no se ubicaban para recibir.
- **Sin pases atrás cerca de nuestra área.**
- **Autopase con un rival cerca.** En el 58% de los turnos con balón (78 de 135) había un rival a menos de 1.2 unidades. Mover el balón casi no se usó: 10 veces, y 6 conservaron el balón al turno siguiente.
- **Despeje largo del portero** cuando tiene el balón.

---

## v2 — Juego de posesión: triangulaciones, autopase y despeje largo

**Desplegado en AgentCore:** versión 4 de los runtimes, 2026-10-28.
**Ojo:** son 4 cambios juntos, por pedido del entrenador. Cada uno se mide por separado con las razones del filtro (`filtro <pos> {...}` en el reporte).

### Cambios

Todo está en `lib/tactics.py`. Lo activa `team_play=True` en los 5 agentes; los equipos de ejemplo de AWS no cambian.

| Pedido | Qué hace el código | Qué ve el modelo |
|---|---|---|
| Triangulaciones | Con el balón en nuestro equipo, cada jugador sin balón tiene un `supportSpot`: FWD1 adelante a la izquierda del que lleva el balón, FWD2 adelante a la derecha, MID atrás en diagonal y DEF de seguridad sin pasar la mitad. Si el modelo pide presionar el balón propio, va a ese punto. | `TEAMMATE HAS THE BALL: your supportSpot=(x,y)` |
| Sin pases atrás en zona propia | En nuestro tercio del campo, un pase hacia atrás (incluido al portero) se cambia por el mejor pase adelante, o por una conducción si no hay pase. | Opciones de pase marcadas `FORWARD`/`BACK`, `open`/`marked`, `BLOCKED` y `FORBIDDEN`, más `bestPass` |
| Autopase con presión | Con un rival a menos de ~1.2 unidades, el jugador conduce hacia `dribbleTo`, el punto adelante más alejado de los rivales. Se mantiene la decisión del modelo si es un pase abierto o un tiro en rango. | `underPressure=True nearestOpponent=d` y `dribbleTo=(x,y)` |
| Despeje largo | El portero con balón siempre hace `GK_DISTRIBUTE` con `KICK` al compañero más adelantado y libre. | `clear it long: ... KICK to player N` |

Prueba local con el modelo real: MID con presión eligió un pase en profundidad abierto, FWD1 fue exacto a su `supportSpot`, DEF en su tercio pasó adelante y GK pateó largo.

### Qué esperamos ver

| Métrica | v1 | Esperado en v2 |
|---|---|---|
| Razón `back pass in our defensive third` | – | Pocas: el modelo ya ve `FORBIDDEN` |
| Razones `under pressure...` | – | Algunas por partido. Revisar si al turno siguiente seguimos con el balón. |
| Razón `goalkeeper clears long` | – | Cada vez que el GK atrape y el modelo no patee |
| Goles a favor | 0 | Más, por triangulación y conducción hacia el arco |
| Goles en contra | 1 | Igual o menos |

### Resultados

Un partido, el 4: derrota 0-1, con el gol en contra recién a los 182 s. Aguantamos 3 minutos sin recibir.

| Métrica | v1 (partido 3) | v2 (partido 4) |
|---|---|---|
| Correcciones por presión (conducir) | – | 23: DEF 5, MID 6, FWD1 4, FWD2 8 |
| Pases atrás en nuestro tercio corregidos | – | 1, porque el modelo ya respeta `FORBIDDEN` |
| DEF presiona el balón propio | 22 | 9 |
| GK pide sacar sin balón | 19 | 33 |
| Tiros pedidos FWD1 / FWD2 | 7 / 11 | 2 / 9; 8 de los de FWD2 se cambiaron por conducciones |

### Qué mejorar → v3

Lo que vio el entrenador en el partido 4:

- **"El delantero no tira; que considere tiros de media distancia."** Causa: el filtro de presión solo dejaba tirar dentro de `shootRange` (3.0 unidades) y cambió 8 tiros de FWD2 por conducciones.
- **"Mis jugadores a veces van hacia atrás en vez de adelante cuando están en ataque."** Causa: en v2 el punto de triángulo del MID estaba detrás del que lleva el balón, y nada impedía retroceder.
- **"El portero a veces deja su posición."** Causa: nada limitaba sus `MOVE_TO` (moverse) ni `INTERCEPT` (interceptar); el modelo pidió 43 movimientos y 7 intercepciones.

---

## v3 — Tiros de media distancia, portero en su arco y sin retroceder en ataque

**Desplegado en AgentCore:** versión 5 de los runtimes, 2026-10-28.

### Cambios

- **Tiros de media distancia:** `SHOOT_RANGE` pasa de 25 a 35 unidades de referencia (4.2 en el campo real, un tercio del largo). Dentro del rango se tira aunque haya presión; la decisión del código tira con potencia 1.0.
- **Portero atado al arco:** si el balón no está cerca de nuestro arco (14 unidades de referencia, 1.7 reales), el código cambia por su posición de guardia (entre el balón y el centro del arco) cualquier intento de presionar, interceptar, marcar, seguir o barrerse, y cualquier `MOVE_TO` a más de 9 unidades de referencia (1.1 reales) de su línea. Razón en el reporte: `goalkeeper stays home`.
- **Sin retroceder en ataque:** con el balón en nuestro equipo, MID, FWD1 y FWD2 no pueden correr hacia su propio arco; van a su `supportSpot` (`backward run while attacking`). El que lleva el balón no conduce hacia atrás (`carrying the ball backward`). El DEF sí puede, porque es la seguridad.
- **MID** se ubica a la par del que lleva el balón y abierto, ya no detrás.

### Qué esperamos ver

| Métrica | v2 | Esperado en v3 |
|---|---|---|
| Tiros de FWD1 + FWD2 | 11 pedidos | Más, y menos correcciones `under pressure` sobre tiros |
| Razón `goalkeeper stays home` | – | Aparece; el GK ya no sale lejos sin motivo |
| Razón `backward run while attacking` | – | Aparece al principio; idealmente baja a medida que el modelo usa `supportSpot` |
| Goles a favor | 0 | Más de 0 |

### Resultados

_Pendiente._

### Qué mejorar → v4

_Llenar después de los partidos de v3._

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
