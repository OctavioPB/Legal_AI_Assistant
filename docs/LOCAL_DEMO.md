# Demo local (100 % en esta máquina)

Nada sale de tu equipo: Postgres y Redis en contenedores locales, los modelos en Ollama, la API y la
web en `127.0.0.1`. Decisión: ADR-014.

## Requisitos
- Docker Desktop, `uv`, Node 22+, Ollama.
- Modelos de Ollama: `bge-m3` (embeddings), `gemma4:26b-a4b-it-qat` (redacción), `qwen3:4b-instruct-2507-q4_K_M`
  (utilidad/respaldo). Si falta alguno: `ollama pull <nombre>`.
- Navegador Edge (solo para las pruebas E2E).

## Primera vez
```bash
docker compose up -d --wait postgres redis        # infraestructura
uv sync && uv run alembic upgrade head            # esquema, roles y RLS
uv run python -m app.doctor                       # 29 comprobaciones de invariantes
uv run python -m app.orchestration.pins           # fija los modelos locales (digest, ventana, fecha)
uv run python -m app.ingestion.seed               # corpus: 8 documentos, ~19 mil chunks, ~9 min
uv run python -m app.demo                         # despacho "demo" + usuario; contraseña UNA vez
cd web && npm install && npm run build && cd ..
```
La contraseña del usuario demo se imprime una sola vez; guárdala en `.demo-credentials` (ignorado por git)
con el formato `firm: demo`, `email: ...`, `password: ...` para que lo lea `npm run e2e:full`.
Se puede regenerar con `uv run python -m app.demo --reset-password`.

## Cada vez
```bash
docker compose up -d --wait postgres redis
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000          # API
cd web && API_URL=http://127.0.0.1:8000 INSECURE_LOCAL=1 npx next start -p 3100   # web
```
Abre http://localhost:3100, firma como `demo` / `owner@demo.local`.

`INSECURE_LOCAL=1` solo quita el atributo `Secure` de la cookie de sesión, necesario si no usas HTTPS;
no lo uses fuera de tu equipo. La cookie sigue siendo `httpOnly` y `SameSite=Strict`.

## Qué esperar
- Una consulta con respuesta tarda **1.5–4 minutos**: el modelo de redacción (26B) corre en gran parte en
  RAM porque la GPU tiene 8 GB, y una respuesta se genera una vez y, si el validador la rechaza, se
  regenera una vez. Mientras tanto la interfaz muestra la etapa actual.
- Un tema que el corpus no cubre se **rechaza en ~3 s** sin llamar al modelo (compuerta τ).
- Un rechazo no es un error: la interfaz explica qué falta y qué hacer. El texto lo pone el sistema.
- Los modelos locales no son deterministas de una corrida a otra aunque la temperatura sea 0: la misma
  consulta puede responderse o rechazarse. Ver la medición en PLAN.md (2026-11-07).

## Pruebas
```bash
uv run pytest                          # 1 900+ pruebas; requiere Postgres levantado
uv run pytest -m live                  # contra el Ollama real (lento)
cd web && npm test && npx tsc --noEmit
cd web && npm run e2e                  # navegador real, sin modelos (API caída a propósito)
cd web && npm run e2e:full             # navegador real + API + modelos locales (minutos)
```

## Límites conocidos (honestos)
- τ está calibrado con conjuntos de prueba redactados por IA y **sin revisión de un abogado**; ningún τ
  cumple a la vez falsos rechazos ≤ 5 % y rechazo correcto ≥ 95 % con el coseno de bge-m3.
- Una cita verificada prueba procedencia, no fidelidad (una cita correcta con un resumen invertido pasa).
- La ingesta de documentos del despacho (subir PDFs) no está construida; el corpus es público y se siembra
  por línea de comandos.
- No hay `docker compose` para la API/web: se levantan a mano como arriba.
