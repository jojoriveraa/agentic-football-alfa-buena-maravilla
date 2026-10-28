# Cómo llevar la bitácora

La [bitácora](bitacora.md) responde tres preguntas por versión: **qué cambiamos**, **cómo nos fue** y **qué mejorar**.

## Después de cada partido

1. Activa las credenciales del taller y corre el análisis desde la raíz del repo:

   ```bash
   python3 tools/analizar_partido.py \
     --json .docs/partidos/AAAA-MM-DD-pN-rival.json \
     | tee .docs/partidos/AAAA-MM-DD-pN-rival.txt
   ```

   Sin `--session` toma el partido más reciente. Para uno anterior, pasa una parte de su id de sesión (aparece en la primera línea del reporte).

2. Agrega una fila en la tabla **Partidos** de `bitacora.md` con:
   - la versión, el rival y el marcador;
   - la posesión y los tiros del Match Report;
   - los enlaces al `.txt` y al `.json`.
3. Actualiza la fila de la versión en **Resumen**: partidos jugados, G-E-P (ganados, empatados, perdidos) y goles.

## Cuando cambias el código

1. Abre una sección nueva `## vN — <cambio>` en `bitacora.md`, con:
   - **Commit:** el hash del commit;
   - **Cambios:** qué archivos y por qué;
   - **Qué esperamos ver:** qué columnas del reporte deberían moverse;
   - **Resultados** y **Qué mejorar**, que se llenan después de jugar.
2. Haz **un cambio por versión** y juega 2 o 3 partidos contra el mismo rival antes de concluir.

## Columnas del reporte de `analizar_partido.py`

| Columna | Qué es |
|---|---|
| `llam` | Veces que la plataforma llamó al agente |
| `ok` | Respuestas del modelo que se pudieron leer |
| `filtro` | Respuestas que el filtro cambió por la lógica de respaldo (comando imposible) |
| `ajuste` | Movimientos (`MOVE_TO`) reescalados o recortados para quedar dentro del campo |
| `resp` | Veces que se usó la lógica de respaldo porque la respuesta no se pudo leer |
| `err` | Errores del agente |
| `p50` | Latencia típica (mediana), en segundos |
| `conBalon` | Turnos en los que el jugador tenía el balón |
| `sinBalon` | Turnos en que el modelo pidió pase, tiro o saque sin tener el balón |
| `propio` | Turnos en que el modelo pidió presionar o interceptar con el balón en nuestro equipo |

## Nunca subas

Credenciales de AWS, el código de equipo del Player Portal ni los ARN (el identificador de cada recurso en AWS) o el número de la cuenta del taller. El repositorio es público.
