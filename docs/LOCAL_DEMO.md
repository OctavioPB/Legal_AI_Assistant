# Local demo (100 % on this machine)

Nothing leaves your computer: Postgres and Redis in local containers, the models in Ollama, the API and the
web app on `127.0.0.1`. Decision: ADR-014.

The interface is in Spanish (es-MX) by default; the labels quoted below («Iniciar Legal AI.cmd», "Ajustes", "Ver el
texto extraído", ...) are the ones you will see on screen.

## Requirements
- Docker Desktop, `uv`, Node 22+, Ollama.
- Ollama models: `bge-m3` (embeddings), `gemma4:26b-a4b-it-qat` (drafting), `qwen3:4b-instruct-2507-q4_K_M`
  (utility/fallback). If one is missing: `ollama pull <name>`.
- Edge browser (only for the E2E tests).
- Let Ollama keep more than one model loaded (ADR-041): set `OLLAMA_MAX_LOADED_MODELS=3` for your user
  (`[Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS','3','User')`) and restart Ollama. Without it,
  every answer unloads the drafting model to embed the question and reloads it to generate: measured on an 8 GB
  GPU, the median answer went from 61 s to 28 s with the setting. `scripts/dev-up.ps1` sets it when it starts
  Ollama itself and warns otherwise; `doctor` checks it.
- Tesseract 5 with Spanish and English data, to read scanned PDFs (ADR-030). Without it everything else works and
  a scanned upload fails with a clear message; `uv run python -m app.doctor` warns.
  - Windows: `winget install UB-Mannheim.TesseractOCR`, then put `spa.traineddata`, `eng.traineddata` and
    `osd.traineddata` from [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) in
    `%LOCALAPPDATA%\legalai\tessdata` (Markdown-Conversor's `%LOCALAPPDATA%\tomd\tessdata` is used too).
  - Debian/Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-spa`. macOS: `brew install tesseract tesseract-lang`.
  - Other locations: `OCR_TESSERACT_CMD` and `OCR_TESSDATA` in `.env`.

## With a double click (for anyone)
In the project folder: **`Iniciar Legal AI.cmd`** prepares everything and opens the browser; **`Detener Legal AI.cmd`**
shuts it down (the data is kept). If something is missing, the script asks before downloading it and explains what to do.
Inside the app, the start page explains the three options; nothing needs to be configured for the first
question (by default: Mexican federal law, general profile, in-force rules only).

## With a single command (recommended for whoever administers)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1             # installs what is missing, prepares and launches
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Status     # what is up and at which address
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Stop       # stops the API and web app (frees 8000/3100)
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Stop -All  # also Postgres/Redis (the data stays)
```
It is idempotent: the second time it only relaunches the API and web app. **It asks before downloading** (`-Yes` to not
ask): it installs with winget whatever is missing (Docker Desktop, uv, Node, Ollama; `-NoInstall` to only warn)
and the missing models (gemma4 26B weighs ~17 GB),
seeds the corpus only if the database is empty and saves the demo user's password in
`.demo-credentials`. If another program holds a port, it stops and says which one: it never kills other people's processes.
It opens no firewall ports: everything listens on `127.0.0.1`. If the installed models do not match their
pins (another digest), it stops: that is a model change (golden + recalibrate τ), not something that fixes itself.
Logs in `.run/`.

## Step by step (the same as the script does)
```bash
docker compose up -d --wait postgres redis        # infrastructure
uv sync && uv run alembic upgrade head            # schema, roles and RLS
uv run python -m app.doctor                       # 31 invariant checks
uv run python -m app.orchestration.pins           # pins the local models (digest, window, date)
uv run python -m app.ingestion.seed               # corpus: 8 documents, ~19 thousand chunks, ~9 min
uv run python -m app.demo                         # "demo" firm + user; password shown ONCE
cd web && npm install && npm run build && cd ..
```
The demo user's password is printed only once; save it in `.demo-credentials` (ignored by git)
with the format `firm: demo`, `email: ...`, `password: ...` so that `npm run e2e:full` can read it.
It can be regenerated with `uv run python -m app.demo --reset-password`.

## Every time
```bash
docker compose up -d --wait postgres redis
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000          # API
cd web && API_URL=http://127.0.0.1:8000 INSECURE_LOCAL=1 npx next start -H 127.0.0.1 -p 3100   # web
```
Open http://127.0.0.1:3100 and sign in as `demo` / `owner@demo.local`.

`INSECURE_LOCAL=1` only removes the `Secure` attribute from the session cookie, which is needed if you do not use HTTPS;
do not use it outside your machine. The cookie remains `httpOnly` and `SameSite=Strict`.

## The three pages (ADR-016)
- **Conversar** (`/conversar`): questions and follow-ups. Each message is searched as it is written; the sources already
  cited in the conversation remain available for the follow-up and the model sees the last turns.
- **Investigación** (`/investigacion`): each question produces a file (statement of the question, legal framework,
  criteria, analysis, sources). "Exportar PDF" uses the browser's print, with the citations numbered.
- **Argumentar** (`/argumentar`): you create a case (facts, the party you represent, the claim) and work in rounds:
  "Enviar a la contraparte" returns the opposing lawyer's attack with its prioritized vulnerabilities;
  "Fortalecer mi argumento" returns the best argument with the sources and the points to reinforce.

Jurisdiction, profile and language are fixed when each conversation or case is started. The history (left bar)
is per user and is stored in the local Postgres; it can be renamed and deleted.

## Tools for lawyers (ADR-017/018/019/025)
- **Leyes** (`/leyes`): a navigator for each loaded law by titles, chapters and articles; "Ir al artículo" by
  number; citable parts (copy the citation, save to a matter) and comparison with earlier versions. No model.
- **Verificar** (`/verificar`): paste or upload a brief (a PDF with text) and check each cited article: whether it exists,
  whether it is in force, whether the quotation matches. **The brief is not stored**; only the report (references and
  results). Laws that are not loaded are reported as such, never guessed.
- **Key-points sheet** (inside a matter, ADR-028): "Generar hoja de puntos clave" reads the matter's conversations,
  notes and saved sources and writes a one-page sheet with Puntos clave, Riesgos, Preguntas para
  el cliente and Temas a revisar. It can only cite sources the matter has already cited, and it goes through the same
  verification as an answer: if a point cannot be verified, the system rejects the sheet instead of delivering it. The questions
  and topics are marked with ◇ (the assistant's reasoning, with no normative support). "Abrir para imprimir o
  guardar en PDF" opens the sheet in letter size; use the browser's "Guardar como PDF". With the local model it takes
  from one to several minutes. A matter with no cited sources produces no sheet.
- **Asuntos** (`/asuntos`): a folder per matter with conversations, research files, cases, verifications, saved
  sources and notes; only its owner and the colleagues they add can see it (isolation in the database, ADR-018).
- **Documentos** (`/documentos`): upload a contract, a brief, a ruling or a rule that is not loaded
  (a PDF with text, a Word `.docx` or plain text, up to 25 MB). It becomes citable in conversations just like the law;
  the citation names the document's section («Contrato», CUARTA.- PENA CONVENCIONAL).
  - **Who sees it.** Without a matter: the whole firm. Inside a matter: only its members, and it is used only in the
    conversations of that matter (isolation in the database, ADR-025). Deleting the matter deletes its documents.
  - **What is stored.** Not the original file; only the extracted text. "Ver el texto extraído" shows what the system
    read, part by part: review it, because what is missing there the assistant does not see either.
  - **Scope.** It is filed under a scope from the catalog: a document marked as Nuevo León does not appear in a
    federal conversation or one of another state.
  - A short document is ready in seconds; a long PDF, in minutes. The list updates itself.
  - **Scanned PDFs** (ADR-030): pages without a text layer are read by OCR on this machine (3-4 s per page). The
    list shows which pages were read by OCR and their mean confidence; a page read with low confidence is left out
    (the assistant never sees or cites it) and listed so it can be rescanned at 300 dpi or more. If the PDF has a
    text layer that is garbage, tick "El texto del PDF está dañado: leerlo como escaneado". The source viewer
    marks text that came from OCR: compare it with the paper before citing it.
  - It is deleted by whoever uploaded it or an administrator; the conversations that cited it keep the citation's name.
- **Reformas** (`/reformas`): the watcher checks the official sources of the 8 loaded laws
  (diputados.gob.mx for LFT, CPEUM, CCF and CPF; hcnl.gob.mx for the Código Civil de NL; monterrey.gob.mx for the
  two regulations; the official leginfo archive for California's Labor Code, read in parts without
  downloading it whole). If a law changed, it loads the new version without deleting the previous one and says which
  conversations, saved sources or verifications cite reformed provisions. A version can also
  be dropped in by hand at `data/corpus/inbox/{CODE}.pdf` (or `.txt`). It is the only piece with a network, and only toward those URLs;
  if a source rejects the download, it is noted and not insisted on.
  - **New laws (ADR-026).** It also reads daily the official list of federal laws in force from Diputados and
    shows in `/reformas` the laws that appeared since the first reading and the full catalog. Reading the
    list downloads no law. To load one, whoever administers the machine approves it:
    `uv run python -m app.ingestion.discover list --status new` (or `--search aguas`) and
    `uv run python -m app.ingestion.discover approve DIP_FED:LGAg --domain environmental --by "Your name"`.
    The next check downloads and loads it; from then on its reforms are also watched.
    `discover dismiss DIP_FED:CODE --by ...` discards it. Valid domains: those of `LEGAL_DOMAINS` in
    `app/ingestion/corpus.py`. The brief verifier does not yet recognise laws approved this way by name
    (they are searched and cited in conversations, but "art. 5 de la Ley General de Aguas" in a brief comes out as an
    unidentified law until its name is added to the grammar).
  - **News** (`/noticias`, ADR-027): a feed of up to 30 legal items updated in the same daily check. It includes the
    reforms the watcher loaded, the new laws and recent reforms from the official index, Supreme Court press
    releases and press items (Nexos "El juego de la Corte"; from El Financiero and La Jornada only those that mention
    legal terms). They are ordered by fixed rules (official source first, legal terms, date); the model plays no part and each
    item is shown in the words of its source with its link. By hand: `uv run python -m app.news.pipeline` (`--dry-run` shows the feed without
    saving it). If there were few legal news items that week, the feed shows fewer than 30: it is not padded.
  - **Daily canary on GitHub (ADR-029).** Besides the load on this machine, GitHub checks every day, writing nothing,
    that the official and news sources respond and that their readers still understand them
    (`.github/workflows/etl-canary.yml`). If one fails, it opens or comments on the issue «ETL canario: fuentes con
    problemas»; when everything responds again, it closes it. By hand: `uv run --no-project --isolated python -m
    app.ingestion.canary`. A source that blocks data-centre addresses may show as blocked on
    GitHub and work here.
  - When the demo starts, it checks in the background whether the last check is more than 20 h old.
  - Daily check (03:00, or when the machine is switched on): `dev-up.ps1 -ScheduleReforms`; to remove it:
    `-UnscheduleReforms`. By hand: `dev-up.ps1 -WatchReforms -IfStaleHours 0` (`-DryRun` writes nothing).
  - Log: `.run\watch-YYYYMMDD.log`. `doctor` warns if the watcher has not run for 3 days, if a source is
    blocked or if τ was calibrated before the latest reform.

## Choosing the model (ADR-024)
When creating a conversation, research file or case, "Modelo" appears with the models the firm may use; it stays
fixed for that conversation and is shown as a chip next to the scope. Each model shows its measurement in the golden
evaluation or "Sin medir".
- **Gemma 4 · 26B** (default) and **Qwen 3 · 4B**: on this machine; nothing is installed.
- **Claude Opus 5.5** (external, optional). The question, the history, the case facts and the retrieved
  fragments are sent to Anthropic. To enable it:
  1. Put `ANTHROPIC_API_KEY=...` in `.env` (never in the repository).
  2. `uv add anthropic` (downloads the official SDK; the local demo does not need it).
  3. Restart the API; a firm administrator turns on Ajustes › "Modelos externos".
  When it is turned off, conversations with Claude can no longer continue.
- To measure a model: `uv run python -m evals.run --suite golden --model-profile qwen` (with Claude it costs money:
  25 cases) and copy the numbers from the report into `config/model_measurements.yaml`.

## An account for presenting
Separate from `demo` (which the E2E tests use), with presentable names and a password that is easy to type
live (`word-word-word-1234`, random):
```bash
uv run python -m app.demo --slug presentacion --name "Despacho Presentación" --email presentacion@demo.local     --display-name "Lic. Presentación" --memorable --save .presentation-credentials
```
Sign in at http://127.0.0.1:3100 with firm `presentacion`, email `presentacion@demo.local` and the password
from `.presentation-credentials` (ignored by git; only the hash is in the database). New password: the same
command with `--reset-password`. Being another firm, it serves to show the isolation: what one sees the other does
not (`demo`). Everything is local: the account exists only in this machine's Postgres.

## Sample case for review (E2E-LAB-01)
In the `demo` firm there is the matter **«Martínez Solís vs. Distribuidora Ficticia del Norte (CASO FICTICIO
E2E-LAB-01)»**: a labor dismissal run through all the tools (six queries, a research file, two
rounds of Argumentar, a verification, the firm's contract and the key-points sheet). It serves to show the
application with a complete case, or for a lawyer to walk through it on screen while grading the review
packet. What it contains, what came out and how to repeat it: [EVALUACION-E2E.md](EVALUACION-E2E.md). To create it again
on another machine: `uv run python -m evals.e2e.run_case laboral_01 --credentials .demo-credentials --keep`, with the API
running (about 17 minutes with the local model).

## What to expect
- A query with an answer takes **about half a minute to a minute** with `OLLAMA_MAX_LOADED_MODELS=3` (47 s median
  over the answered cases of the golden run of 2026-11-27; 1.5–4 minutes without it, because Ollama then swaps the
  embedder and the drafting model on every turn, ADR-041). The drafting model (26B) runs largely in RAM because the
  GPU has 8 GB, and an answer is generated once and, if the validator rejects it, is regenerated once. Meanwhile the
  interface shows the current stage.
- A topic the corpus does not cover is **refused in ~3 s** without calling the model (the τ gate).
- A refusal is not an error: the interface explains what is missing and what to do. The text is written by the system.
- Local models are not deterministic from one run to the next even at temperature 0: the same
  query may be answered or refused. See the measurement in PLAN.md (2026-11-07).

## After a parser or chunker change (index generations, ADR-006)
The index is versioned as a whole. A change that alters chunks is built aside, measured, and only then switched in:
```bash
uv run python -m app.ingestion.rebuild status          # which generation is active, and its chunker version
uv run python -m app.ingestion.rebuild build --note "what changed"   # ~15 min: re-reads every law, re-embeds
uv run python -m app.ingestion.rebuild activate N      # switch in (one transaction), then run the golden suite
uv run python -m app.ingestion.rebuild rollback        # back to the previous generation if any case is worse
```
- Until the new generation is active, the app refuses to ingest with the new chunker (doctor says so): uploads and
  the reform watcher write only into a generation cut by the same code.
- The build stops instead of writing a different corpus: a law whose source no longer matches its stored checksum,
  or a firm document whose units would change, is never rebuilt silently.
- On Windows the embedder can run out of local sockets during a build; the rebuild pauses and retries the batch.
- `fill N` adds documents uploaded while N was being built; `prune N` deletes a retired generation's rows.

## Tests
```bash
uv run pytest                          # 2,100+ tests; requires Postgres up
uv run pytest -m live                  # against the real Ollama (slow)
cd web && npm test && npx tsc --noEmit
# real browser, no models: the interface against a nonexistent API (catalogs simulated in the browser)
cd web && (API_URL=http://127.0.0.1:8999 npx next start -H 127.0.0.1 -p 3101 &) && BASE_URL=http://127.0.0.1:3101 npm run e2e
cd web && npm run e2e:full             # real browser + API + local models (minutes)
```

## Known limits (honest)
- τ is calibrated with probe sets written by AI and **without a lawyer's review**; no τ
  meets both false refusals ≤ 5 % and correct refusal ≥ 95 % with the bge-m3 cosine.
- A verified citation proves provenance, not fidelity (a correct citation with an inverted summary passes).
- The firm's documents: OCR text can contain misread words even above the confidence threshold, and the brief
  verifier does not read scans; no `.docx` footnotes, and the reading of
  contracts and rulings is by form (clauses, headings, paragraphs) and **without a lawyer's review**. τ was
  calibrated with statute text, not with contracts. If the app is closed halfway through a load, the document is left
  "interrumpido" and has to be uploaded again.
- The public corpus is seeded from the command line; no case law or theses are loaded.
- The API and web app do not run in containers: `scripts/dev-up.ps1` launches them (or by hand, as above).
- `-H 127.0.0.1` is mandatory when launching the web app by hand: without it, `next start` listens on all interfaces
  and the app is visible on the local network.
