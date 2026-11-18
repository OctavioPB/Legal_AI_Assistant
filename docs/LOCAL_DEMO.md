# Demo local (100 % en esta máquina)

Nada sale de tu equipo: Postgres y Redis en contenedores locales, los modelos en Ollama, la API y la
web en `127.0.0.1`. Decisión: ADR-014.

## Requisitos
- Docker Desktop, `uv`, Node 22+, Ollama.
- Modelos de Ollama: `bge-m3` (embeddings), `gemma4:26b-a4b-it-qat` (redacción), `qwen3:4b-instruct-2507-q4_K_M`
  (utilidad/respaldo). Si falta alguno: `ollama pull <nombre>`.
- Navegador Edge (solo para las pruebas E2E).

## Con doble clic (para cualquier persona)
En la carpeta del proyecto: **`Iniciar Legal AI.cmd`** prepara todo y abre el navegador; **`Detener Legal AI.cmd`**
la cierra (los datos se conservan). Si falta algo, el script pregunta antes de descargarlo y explica qué hacer.
Dentro de la app, la página de inicio explica las tres opciones; no hace falta configurar nada para la primera
pregunta (por omisión: derecho federal de México, perfil general, solo normas vigentes).

## Con un solo comando (recomendado para quien administra)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1             # instala lo que falte, prepara y lanza
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Status     # qué está arriba y en qué dirección
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Stop       # cierra API y web (libera 8000/3100)
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Stop -All  # además Postgres/Redis (los datos quedan)
```
Es idempotente: la segunda vez solo relanza API y web. **Pregunta antes de descargar** (`-Yes` para no
preguntar): instala con winget lo que falte (Docker Desktop, uv, Node, Ollama; `-NoInstall` para solo avisar)
y los modelos que falten (gemma4 26B pesa ~17 GB),
siembra el corpus solo si la base está vacía y guarda la contraseña del usuario demo en
`.demo-credentials`. Si un puerto lo ocupa otro programa, se detiene y dice cuál: nunca mata procesos ajenos.
No abre puertos en el firewall: todo escucha en `127.0.0.1`. Si los modelos instalados no coinciden con sus
pins (otro digest), se detiene: eso es un cambio de modelo (golden + recalibrar τ), no algo que se arregle solo.
Logs en `.run/`.

## Paso a paso (lo mismo que hace el script)
```bash
docker compose up -d --wait postgres redis        # infraestructura
uv sync && uv run alembic upgrade head            # esquema, roles y RLS
uv run python -m app.doctor                       # 31 comprobaciones de invariantes
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
cd web && API_URL=http://127.0.0.1:8000 INSECURE_LOCAL=1 npx next start -H 127.0.0.1 -p 3100   # web
```
Abre http://127.0.0.1:3100, firma como `demo` / `owner@demo.local`.

`INSECURE_LOCAL=1` solo quita el atributo `Secure` de la cookie de sesión, necesario si no usas HTTPS;
no lo uses fuera de tu equipo. La cookie sigue siendo `httpOnly` y `SameSite=Strict`.

## Las tres páginas (ADR-016)
- **Conversar** (`/conversar`): preguntas y seguimientos. Cada mensaje se busca tal como se escribe; las fuentes ya
  citadas en la conversación siguen disponibles para el seguimiento y el modelo ve los últimos turnos.
- **Investigación** (`/investigacion`): cada pregunta produce un expediente (planteamiento, marco normativo,
  criterios, análisis, fuentes). "Exportar PDF" usa la impresión del navegador, con las citas numeradas.
- **Argumentar** (`/argumentar`): se crea un caso (hechos, parte que representa, pretensión) y se trabaja por rondas:
  "Enviar a la contraparte" devuelve el ataque del abogado contrario con sus vulnerabilidades priorizadas;
  "Fortalecer mi argumento" devuelve el mejor argumento con las fuentes y los puntos a reforzar.

La jurisdicción, el perfil y el idioma se fijan al iniciar cada conversación o caso. El historial (barra izquierda)
es por usuario y se guarda en el Postgres local; se puede renombrar y eliminar.

## Herramientas para abogados (ADR-017/018/019/025)
- **Leyes** (`/leyes`): navegador de cada ley cargada por títulos, capítulos y artículos; "Ir al artículo" por
  número; partes citables (copiar cita, guardar en asunto) y comparación con versiones anteriores. Sin modelo.
- **Verificar** (`/verificar`): pegue o suba un escrito (PDF con texto) y revise cada artículo citado: si existe,
  si está vigente, si la cita textual coincide. **El escrito no se guarda**; solo el reporte (referencias y
  resultados). Las leyes no cargadas se reportan como tales, nunca se adivinan.
- **Hoja de puntos clave** (dentro de un asunto, ADR-028): "Generar hoja de puntos clave" lee las conversaciones,
  notas y fuentes guardadas del asunto y escribe una hoja de una página con Puntos clave, Riesgos, Preguntas para
  el cliente y Temas a revisar. Solo puede citar fuentes que el asunto ya citó, y pasa por la misma verificación
  que una respuesta: si un punto no se puede verificar, el sistema la rechaza en lugar de entregarla. Las preguntas
  y los temas van marcados con ◇ (razonamiento del asistente, sin respaldo normativo). "Abrir para imprimir o
  guardar en PDF" abre la hoja tamaño carta; use "Guardar como PDF" del navegador. Con el modelo local tarda de
  uno a varios minutos. Un asunto sin fuentes citadas no produce hoja.
- **Asuntos** (`/asuntos`): carpeta por asunto con conversaciones, investigaciones, casos, verificaciones, fuentes
  guardadas y notas; solo la ven su responsable y los colegas que agregue (aislamiento en la base, ADR-018).
- **Documentos** (`/documentos`): suba un contrato, un escrito, una resolución o una norma que no esté cargada
  (PDF con texto, Word `.docx` o texto plano, hasta 25 MB). Queda citable en las conversaciones igual que la ley;
  la cita nombra la sección del documento («Contrato», CUARTA.- PENA CONVENCIONAL).
  - **Quién lo ve.** Sin asunto: todo el despacho. Dentro de un asunto: solo sus miembros, y solo se usa en las
    conversaciones de ese asunto (aislamiento en la base, ADR-025). Borrar el asunto borra sus documentos.
  - **Qué se guarda.** El archivo original no; solo el texto extraído. "Ver el texto extraído" muestra lo que
    leyó el sistema, parte por parte: revíselo, porque lo que falte ahí el asistente tampoco lo ve.
  - **Ámbito.** Se archiva bajo un ámbito del catálogo: un documento marcado como de Nuevo León no aparece en una
    conversación federal ni de otro estado.
  - Un documento corto queda listo en segundos; un PDF largo, en minutos. La lista se actualiza sola. Un PDF
    escaneado (solo imágenes) no se puede leer: pase antes un OCR o suba el Word.
  - Lo elimina quien lo subió o un administrador; las conversaciones que lo citaron conservan el nombre de la cita.
- **Reformas** (`/reformas`): el vigilante revisa las fuentes oficiales de las 8 leyes cargadas
  (diputados.gob.mx para LFT, CPEUM, CCF y CPF; hcnl.gob.mx para el Código Civil de NL; monterrey.gob.mx para los
  dos reglamentos; el archivo oficial de leginfo para el Labor Code de California, leído por partes sin
  descargarlo completo). Si una ley cambió, carga la nueva versión sin borrar la anterior y avisa qué
  conversaciones, fuentes guardadas o verificaciones citan disposiciones reformadas. Una versión también puede
  dejarse a mano en `data/corpus/inbox/{CLAVE}.pdf` (o `.txt`). Es la única pieza con red, y solo hacia esas URL;
  si una fuente rechaza la descarga, se anota y no se insiste.
  - **Leyes nuevas (ADR-026).** También lee a diario la lista oficial de leyes federales vigentes de Diputados y
    muestra en `/reformas` las leyes que aparecieron desde la primera lectura y el catálogo completo. Leer la
    lista no descarga ninguna ley. Para cargar una, quien administra el equipo la aprueba:
    `uv run python -m app.ingestion.discover list --status new` (o `--search aguas`) y
    `uv run python -m app.ingestion.discover approve DIP_FED:LGAg --domain environmental --by "Su nombre"`.
    La siguiente revisión la descarga y la carga; desde entonces también se vigilan sus reformas.
    `discover dismiss DIP_FED:CLAVE --by ...` la descarta. Materias válidas: las de `LEGAL_DOMAINS` en
    `app/ingestion/corpus.py`. El verificador de escritos aún no reconoce por nombre las leyes aprobadas así
    (se buscan y se citan en las conversaciones, pero "art. 5 de la Ley General de Aguas" en un escrito sale como
    ley no identificada hasta que se agregue su nombre a la gramática).
  - **Noticias** (`/noticias`, ADR-027): un feed de hasta 30 notas jurídicas que se actualiza en la misma revisión
    diaria. Entran las reformas que cargó el vigilante, las leyes nuevas y las reformas recientes del índice
    oficial, los comunicados de la Suprema Corte y notas de prensa (Nexos "El juego de la Corte"; de El
    Financiero y La Jornada solo las que mencionan términos jurídicos). Se ordenan con reglas fijas (fuente
    oficial primero, términos jurídicos, fecha); el modelo no interviene y cada nota se muestra con las palabras
    de su fuente y su enlace. A mano: `uv run python -m app.news.pipeline` (`--dry-run` muestra el feed sin
    guardarlo). Si en la semana hubo pocas noticias jurídicas, el feed muestra menos de 30: no se rellena.
  - Al iniciar la demo se revisa en segundo plano si la última revisión tiene más de 20 h.
  - Revisión diaria (03:00, o al encender el equipo): `dev-up.ps1 -ScheduleReforms`; quitarla:
    `-UnscheduleReforms`. A mano: `dev-up.ps1 -WatchReforms -IfStaleHours 0` (`-DryRun` no escribe nada).
  - Log: `.run\watch-AAAAMMDD.log`. `doctor` avisa si el vigilante no corre hace 3 días, si una fuente está
    bloqueada o si τ se calibró antes de la última reforma.

## Elegir el modelo (ADR-024)
Al crear una conversación, investigación o caso aparece "Modelo" con los modelos que puede usar el despacho; queda
fijo para esa conversación y se ve como chip junto al ámbito. Cada modelo muestra su medición en la evaluación
golden o "Sin medir".
- **Gemma 4 · 26B** (predeterminado) y **Qwen 3 · 4B**: en este equipo; no se instala nada.
- **Claude Opus 5.5** (externo, opcional). La pregunta, el historial, los hechos del caso y los fragmentos
  recuperados se envían a Anthropic. Para habilitarlo:
  1. Poner `ANTHROPIC_API_KEY=...` en `.env` (nunca en el repositorio).
  2. `uv add anthropic` (descarga el SDK oficial; la demo local no lo necesita).
  3. Reiniciar la API; un administrador del despacho activa Ajustes › "Modelos externos".
  Al desactivarlo, las conversaciones con Claude ya no pueden continuar.
- Medir un modelo: `uv run python -m evals.run --suite golden --model-profile qwen` (con Claude cuesta dinero:
  25 casos) y copiar los números del reporte a `config/model_measurements.yaml`.

## Cuenta para presentar
Separada de `demo` (que usan las pruebas E2E), con nombres presentables y una contraseña fácil de teclear
en vivo (`palabra-palabra-palabra-1234`, aleatoria):
```bash
uv run python -m app.demo --slug presentacion --name "Despacho Presentación" --email presentacion@demo.local     --display-name "Lic. Presentación" --memorable --save .presentation-credentials
```
Entrar en http://127.0.0.1:3100 con despacho `presentacion`, correo `presentacion@demo.local` y la contraseña
de `.presentation-credentials` (ignorado por git; solo el hash está en la base). Nueva contraseña: el mismo
comando con `--reset-password`. Al ser otro despacho, sirve para mostrar el aislamiento: lo que vea uno no lo ve
`demo`. Todo es local: la cuenta solo existe en el Postgres de este equipo.

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
# navegador real, sin modelos: la interfaz contra una API inexistente (catálogos simulados en el navegador)
cd web && (API_URL=http://127.0.0.1:8999 npx next start -H 127.0.0.1 -p 3101 &) && BASE_URL=http://127.0.0.1:3101 npm run e2e
cd web && npm run e2e:full             # navegador real + API + modelos locales (minutos)
```

## Límites conocidos (honestos)
- τ está calibrado con conjuntos de prueba redactados por IA y **sin revisión de un abogado**; ningún τ
  cumple a la vez falsos rechazos ≤ 5 % y rechazo correcto ≥ 95 % con el coseno de bge-m3.
- Una cita verificada prueba procedencia, no fidelidad (una cita correcta con un resumen invertido pasa).
- Documentos del despacho: sin OCR (un PDF escaneado falla), sin notas al pie de `.docx`, y la lectura de
  contratos y sentencias es por forma (cláusulas, encabezados, párrafos) y **sin revisión de un abogado**. τ se
  calibró con texto de leyes, no con contratos. Si la app se cierra a media carga, el documento queda
  "interrumpido" y hay que subirlo otra vez.
- El corpus público se siembra por línea de comandos; no hay jurisprudencia ni tesis cargadas.
- API y web no corren en contenedores: las lanza `scripts/dev-up.ps1` (o a mano, como arriba).
- `-H 127.0.0.1` es obligatorio al lanzar la web a mano: sin él, `next start` escucha en todas las interfaces
  y la app queda visible en la red local.
