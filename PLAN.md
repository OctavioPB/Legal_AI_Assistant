# PLAN.md — Legal AI Assistant: de cero a demo funcional

Estados (columna implícita; se marca en el ID o en el Log): `[ ]` pendiente · `[~]` en curso ·
`[x]` hecho · `[-]` descartado (con ADR o nota). **Fase activa: WS0 → WS2** (WS1 hecho y probado contra Postgres real; WS0.T01/T04–T10 pendientes).
Principio de orden: primero un recuperador **medido sin LLM**, luego generación sobre una
capa de retrieval que ya demostró su valor. Toda medición registra ambos números (antes/después).

Convenciones:
- **WS** = workstream · **T** = task con ID estable (`WS.T##`) para referenciar en PRs/issues.
- `deps:` tareas que deben existir antes. `DoD:` Definition of Done verificable.
- **Regla transversal de UI:** todo desarrollo de Frontend/UI (componentes, layouts, colores,
  tipografía, microcopy, estados vacíos, render de citas) **debe obedecer a `Brand.md`**
  (raíz del repo). Ningún PR de UI se mergea sin declarar conformidad con `Brand.md`;
  si `Brand.md` no cubre el caso, se extiende `Brand.md` primero, no se improvisa en el componente.

Alcance de la demo funcional (criterio de salida global):
un despacho (tenant real) + corpus público sembrado (LFT federal, Código Civil NL,
1 bando municipal Monterrey, 1 statute US/CA), sesión bilingüe con selector de
jurisdicción y persona, Dossier Builder y Adversarial Counterpart operando con citas
verificadas, refusal estructurado visible en UI, y golden suite v1 en verde.

---

## WS0 — Fundaciones del repo y entorno

| ID | Task | deps | DoD |
|---|---|---|---|
| WS0.T01 | Scaffold monorepo (`app/` FastAPI + `web/` Next.js), `uv init`, lockfile, ruff, pre-commit | — | `uv sync && uv run pytest` corre en limpio; CI (lint+tests) en verde |
| WS0.T02 `[x]` | `docker-compose` con Postgres 16 + pgvector + Redis; healthchecks | T01 | `docker compose up -d` deja `psql` con `CREATE EXTENSION vector; ltree` disponibles |
| WS0.T03 `[x]` | Alembic con doble rol (`legalai_migrator` / `legalai_app` sin BYPASSRLS) | T02 | Migración vacía aplica con migrator; app role no puede DDL (test) |
| WS0.T04 | Esqueleto CI: pipeline lint → unit → isolation tests → golden suite (placeholder) | T01 | Pipeline definido; gates documentados aunque suites estén vacías |
| WS0.T05 | `Brand.md` inicial: paleta, tipografía, tono, componentes base, reglas de render de citas y del bloque `<insuficiente>` | — | `Brand.md` en raíz; linkeado desde CLAUDE.md y `.claude/rules/style.md` |
| WS0.T06 | Módulo de settings tipado (pydantic-settings): τ, k, RRF k, presupuestos, umbrales de chunk, retries, aliases; sin valores mágicos en código. `Protocol`s `Embedder`/`Reranker`/`LLMClient`/`ObjectStore` con fakes | T01 | Test falla si un umbral aparece como literal en `app/rag`; suite unit usa solo fakes (`tests/fakes.py` determinista); settings se validan al arrancar (presupuesto de contexto + respuesta ≤ ventana por alias; `*_max_tokens` por llamada); `mypy --strict` en CI |
| WS0.T09 | `app doctor` (`uv run python -m app.doctor`): verifica invariantes ejecutables — extensiones `vector`/`ltree`/`unaccent`; `legalai_app` sin BYPASSRLS ni DDL; ENABLE+FORCE RLS en toda tabla con `tenant_id`; la lectura sin contexto devuelve 0 filas; el brazo léxico pliega acentos; generación de embeddings activa = settings; aliases LiteLLM resuelven a los ids fijados; settings válidos (presupuestos vs ventana). Salida OK/WARN/FAIL | WS1.T03, WS4.T01 | Corre en CI y antes de cada demo; un FAIL bloquea; cada invariante nuevo añade su check |
| WS0.T10 | Bootstrap de desarrollo idempotente (`scripts/dev-up.sh` / `.ps1`): instala lo que falta, `compose up`, migra, siembra, corre `doctor`; pregunta antes de descargar nada | T02, T09 | En máquina limpia deja el entorno verde con un comando; segunda corrida no cambia nada |
| WS0.T07 | `pytest-socket` en CI: unit tests con red deshabilitada; suite `live` separada y fuera del CI por defecto | T01 | Test que intenta abrir un socket falla; pipeline documenta la suite `live` |
| WS0.T08 | Skill/registro de ADRs operativo: formato, estados, regla de "propón antes de implementar" (`decision-record`) | — | ADR-006…009 listados como Proposed en `architecture.md` |

## WS1 — Modelo de datos y multi-tenancy (el perímetro)

| ID | Task | deps | DoD |
|---|---|---|---|
| WS1.T01 `[x]` | Migración: `jurisdictions` (catálogo cerrado) + seed MX (federal, 32 estados, municipios demo) y US (federal, CA) | WS0.T03 | Tabla poblada; constraint UNIQUE verificado |
| WS1.T02 `[x]` | Migraciones: `documents`, `doc_nodes` (ltree), `chunks` (vector 1024, tsvector generado, columnas jurisdiccionales desnormalizadas) + índices (compuesto prefilter, GIN, HNSW) | T01 | `EXPLAIN` de un prefilter usa el índice compuesto; HNSW creado |
| WS1.T03 `[x]` | RLS: políticas read/write en todas las tablas tenant-scoped + `FORCE`; tenant sintético `PUBLIC_CORPUS`; middleware `SET LOCAL app.tenant_id` | T02 | Política visible en `\d+`; query sin contexto devuelve 0 filas |
| WS1.T04 `[x]` | Test harness de aislamiento (`tests/test_tenant_isolation.py`): tenants A/B, intersección vacía, registro de tablas obligatorio | T03 | Test CI-blocking en verde; falla si una tabla nueva carece de políticas |
| WS1.T05 `[x]` | Tablas de producto: `tenants`, `users`, `sessions`, `dossiers`, `audit_log` (append-only) | T03 | CRUD mínimo bajo RLS; audit_log sin UPDATE/DELETE (revoke + test) |

## WS2 — Ingesta (Docling → árbol jurídico → chunks)

| ID | Task | deps | DoD |
|---|---|---|---|
| WS2.T01 | Worker Celery + cola `ingestion`; CLI `ingest <pdf> --jurisdiction --doc-type` | WS0.T02 | Job encolado y procesado end-to-end con PDF dummy |
| WS2.T02 | Wrapper Docling: PDF → DocumentModel; manejo multi-columna; captura de tablas | T01 | Un PDF del DOF multi-columna sale con orden de lectura correcto (fixture) |
| WS2.T03 | Normalizador jurídico MX: gramática Artículo/Fracción/Inciso/Transitorio → `doc_nodes` + ltree + detección de reformas/vigencia | T02 | LFT completa produce árbol correcto (golden tree fixture, diff exacto) |
| WS2.T04 | Normalizador US: §/subsection/holding para statutes y case law | T02 | Statute CA de prueba produce árbol correcto |
| WS2.T05 | Chunker jerárquico: unidad fracción/párrafo, merge-up <80 tk, split >800 tk, breadcrumb prefijado, `cite_id` + `citation_string`; conteo con tokenizer del embedder; `text` = slice exacto con offsets; marca `is_navigation`; verificar ventana de entrada de Cohere embed-v3 vs `max_tokens` | T03 | Property tests: ningún chunk cruza nodos; cobertura sin pérdida/duplicado por artículo; cite_id únicos y parseables; reporte de chunks sobredimensionados; decisión sobre ventana documentada |
| WS2.T06 | Embeddings Cohere embed-v3 (batch, retry, rate-limit) + upsert idempotente por checksum, una transacción por documento; reformas → `is_current=false` versión previa | T05 | Re-ingesta del mismo PDF = 0 duplicados; reforma simulada versiona correctamente; lock por `(tenant, fuente)` con lease; un documento que falla se registra en `failed` sin abortar la corrida; stats de ingesta (added/updated/deleted/unchanged/failed/oversized/seconds) en `audit_log`; chequeo de integridad post-ingesta; re-corrida sin cambios no llama al proveedor de embeddings |
| WS2.T07 | Sembrado del corpus demo: LFT, Código Civil NL, bando Monterrey, statute US/CA | T06 | 4 fuentes consultables en `chunks` con metadata correcta |
| WS2.T08 | Guardas de versión (ADR-006): `embedding_model/revision`, `chunker_version`, umbrales guardados por chunk; retrieval filtra la generación activa; re-embed en generación paralela con swap atómico | T06 | Test: mezclar versiones falla; override de umbrales sin bump de versión es rechazado; fallo a medio re-embed deja la generación viva intacta |
| WS2.T09 | Fixtures patológicas jurídicas + CLI `chunks <pdf>` para inspección manual (lista en `rules/rag.md`) | T05 | ≥ 10 fixtures con test de nº de chunks/paths/no-corte; revisión manual de 10 artículos aleatorios firmada por un abogado |
| WS2.T10 | Medir "caput glue" (lead-in `:` pegado a cada fracción embebida) con la golden suite; si mejora sin regresión, bump de `chunker_version` | T09, WS5.T04 | Corridas antes/después registradas; ADR (Accepted/Rejected) con run ids |

## WS3 — Retrieval (pre-filtering + hybrid + refusal)

| ID | Task | deps | DoD |
|---|---|---|---|
| WS3.T01 | `build_jurisdiction_predicate()` con semántica de apilamiento hacia arriba + property tests (no siblings, stacking, is_current) | WS1.T02 | `tests/test_jurisdiction_predicate.py` en verde; cobertura de casos enumerada |
| WS3.T02 | Brazo semántico (top 40 coseno prefiltrado) y brazo léxico (tsquery por idioma, top 40 prefiltrado) | T01, WS2.T07 | Ambos brazos devuelven resultados correctos sobre corpus demo |
| WS3.T03 | Fusión RRF (k=60 → top 20) + reranker multilingüe (top 8) | T02 | Eval manual: consulta "art. 47 LFT" y consulta conceptual ambas recuperan el gold. **Líneas base registradas por separado:** semántico, léxico, RRF, RRF+rerank (retrieval sin LLM, por categoría); un componente que no mejora se elimina |
| WS3.T04 | Umbral τ post-rerank → refusal path corto-circuito (sin llamada LLM) con payload estructurado generado por código. **τ calibrado con dos probe sets a mano** (temas cubiertos / ausentes, ≥ 20 c/u, varias formulaciones por tema) y guardado con la corrida (ADR-007) | T03 | Consulta fuera de corpus produce refusal sin invocar LiteLLM (assert en test); reporte de scores por tema; decisión sobre "≥ m fragmentos sobre τ" registrada |
| WS3.T07 | Barrido del peso del breadcrumb en el brazo léxico (`setweight`, p. ej. 0.1…4) y exclusión de `is_navigation` en consulta | T03 | Valor elegido sin regresión > 2 pts en ninguna categoría; corridas citadas en el ADR |
| WS3.T05 | Parent expansion vía ltree + dedupe + presupuesto 12k tokens + formato `<fragmento cite_id=...>` | T03 | Contexto emitido contiene artículos íntegros; nunca excede presupuesto |
| WS3.T06 | Query translation solo-léxico para sesiones en-US sobre corpus es (y viceversa); la consulta original conserva sus top-N y la traducida solo rellena (ADR-009), sin RRF igualitario | T02 | Consulta "wrongful termination" recupera art. 47 LFT vía ambos brazos; medido contra RRF igualitario en la categoría bilingüe y en el set monolingüe (sin regresión > 2 pts) |

## WS4 — Orquestación y prompts dinámicos

| ID | Task | deps | DoD |
|---|---|---|---|
| WS4.T01 | LiteLLM proxy self-hosted: aliases (`drafting-primary/heavy`, `adversarial-primary`, `cheap-utility`, `fallback-general`), budgets por tenant, callback Langfuse | WS0.T02 | Llamada a cada alias responde; budget por tenant aplica (test); cada alias fija un id exacto de modelo verificado en la documentación del proveedor (con fecha), no de memoria; el id resuelto queda en cada traza y corrida de eval |
| WS4.T02 | Prompt composer 5 capas + `manifest.yaml` de versiones pinned; carga read-only desde `app/prompts/` | — | Render de prompt completo reproducible byte a byte por versión |
| WS4.T03 | Templates v1: núcleo anti-alucinación, Dossier Builder, Adversarial, personas (labor/criminal/corporate/general), capa sesión | T02 | Sandbox (`evals.prompt_sandbox`) renderiza los 2 workflows × 2 idiomas |
| WS4.T04 | Endpoint `/chat` streaming SSE: SessionContext validado → retrieval → composer → LiteLLM → respuesta | WS3.T05, T01, T03 | Consulta demo end-to-end responde con citas en <10 s p95 sobre corpus demo |
| WS4.T05 | Routing por tarea + override de preferencia de usuario contra allowlist del tenant | T04 | Preferencia válida rutea; preferencia fuera de allowlist se rechaza 403 |

## WS5 — Validación anti-alucinación y evals

| ID | Task | deps | DoD |
|---|---|---|---|
| WS5.T01 | Parser de citas + validación de existencia contra set recuperado de la consulta; rechazo de marcadores huérfanos (`[cite: X]`, `[cite: CITE_ID]`, ids de ejemplo en denylist) | WS4.T04 | Cita inventada inyectada en test es detectada al 100%; marcador de ejemplo copiado del prompt es rechazado |
| WS5.T02 | Validación de cobertura (oración jurídica sin cita ni `[razonamiento estratégico]` = fallo) + **cita textual** (texto entre comillas presente en el fragmento citado tras plegar acentos/mayúsculas/espacios, racha mínima de N palabras configurable) + loop de regeneración (1 retry) → refusal generado por código | T01 | Flujo regeneración/refusal cubierto por tests con LLM mockeado; paráfrasis entrecomillada falla; el refusal nunca lo redacta el modelo |
| WS5.T03 | Audit log por consulta: query, filtros, chunks, output, veredicto | T02, WS1.T05 | Cada respuesta demo genera fila de auditoría completa e inmutable |
| WS5.T04 | Runner de evals (`evals.run`): carga golden JSONL, ejecuta pipeline real, calcula las 6 métricas, reporte JSON + comparador de runs | T02, WS3 | `--suite golden` corre las 4 golden cases sembradas y reporta gates |
| WS5.T05 | Ampliar golden v1 → ~25 casos (6 categorías, ambos idiomas, distractores obligatorios) + `validate_golden`. Campos `authored_by` / `reviewed_by`; casos `ai-draft` se reportan aparte y no cuentan para gates hasta que un abogado los revise; incluir identificadores exactos y casos cross-lingües | T04, WS2.T07 | Suite completa en verde con umbrales de release; integrada a CI; ≥ 50 % de los casos autoría/revisión de abogado |
| WS5.T06 | Protocolo antes/después: `evals.run --tag`, `--compare` por categoría + lista de casos que cambiaron, config efectiva y versiones guardadas en cada corrida; regla de ruido (1–2 casos no es mejora) y regresión > 2 pts bloquea | T04 | Comparador marca regresiones por categoría; PR template exige ambas cifras; registro de corrida con `commit` + `dirty`, ranks por caso, p50/p95, ids de modelo resueltos; preflight que aborta si el golden apunta a chunks inexistentes; categorías dependientes de LLM repetidas ≥ 3× antes de llamar real a un cambio blando |
| WS5.T07 | Hojas de revisión humana (`evals.review_sheet`): cada afirmación junto al texto COMPLETO del fragmento citado; tests del propio harness; registro de acuerdo juez-NLI vs abogado | T04 | Sheet sin truncado (test); gate de fidelidad basado en juez exige acuerdo medido |

## WS6 — Frontend (⚠ todo este WS obedece a `Brand.md`)

| ID | Task | deps | DoD |
|---|---|---|---|
| WS6.T01 | Base Next.js + next-intl (es-MX/en-US), auth (JWT), layout conforme a `Brand.md` | WS0.T05 | Toggle de idioma sin recarga; revisión de conformidad Brand.md en PR |
| WS6.T02 | Selectores de Jurisdicción (país→estado→municipio desde catálogo) y Persona; persistencia en SessionContext | T01, WS1.T01 | Selección inválida imposible desde UI; estados según Brand.md |
| WS6.T03 | Chat streaming con render de citas: `[cite: X]` → chip con `citation_string` + deep-link al PDF (párrafo resaltado) | T02, WS4.T04 | Cita clickeable abre fuente exacta; estilo de chips definido en Brand.md |
| WS6.T04 | Render del refusal `<insuficiente>` como componente de primera clase (qué falta + acciones sugeridas), según Brand.md | T03 | Refusal nunca se muestra como error genérico |
| WS6.T05 | Vista Dossier: secciones I–V, export PDF con lista de fuentes | T03 | Dossier exportado conserva citas y jerarquía tipográfica de Brand.md |
| WS6.T06 | Modo Adversarial: distinción visual `[cite:]` vs `[razonamiento estratégico]`, panel "Vulnerabilidades priorizadas" | T03 | Ambos tipos de marca visualmente distinguibles según Brand.md |
| WS6.T07 | Pantalla de ingesta del despacho: upload de expedientes (client_file), estado del job Celery | T01, WS2.T01 | Upload → procesado → consultable, todo bajo tenant real |
| WS6.T08 | Guardas de UI en CI: paridad de catálogos es-MX/en-US, sin literales de texto fuera del catálogo, sin assets/fuentes externos (self-hosted), fragmentos/respuestas renderizados como texto (nunca HTML crudo) | T01 | Los tres tests fallan ante una clave faltante, un literal o una URL externa |

## WS7 — Seguridad, observabilidad y hardening de demo

| ID | Task | deps | DoD |
|---|---|---|---|
| WS7.T01 | S3 con prefijos por tenant + presigned URLs; IAM espejo de RLS | WS1.T03 | Test: presigned URL de tenant A no sirve objeto de tenant B |
| WS7.T02 | Langfuse: traza completa por consulta (prompt compuesto, chunks, output, veredicto del validator) | WS5.T03 | Una consulta demo es reconstruible 100% desde la traza |
| WS7.T03 | Rate limiting por tenant/usuario en gateway; timeouts y fallbacks LiteLLM verificados con caos básico (matar proveedor primario) | WS4.T05 | Caída simulada de proveedor primario → fallback responde |
| WS7.T04 | Revisión de seguridad de prompts: suite de inyección (instrucciones dentro de fragmentos, nombres de estado maliciosos, `[cite: X]` literal dentro de un fragmento) | WS5.T04 | Casos de inyección añadidos al golden como categoría; en verde |
| WS7.T05 | Alertas de tripwires de ADR (p. ej. nº de chunks vs umbral de ADR-001, ventana de embedder, % de regeneraciones/refusals por alias) | WS7.T02 | Cada ADR con umbral tiene su alerta; test que lista los umbrales declarados vs alertas configuradas |
| WS7.T06 | Registro único de almacenes de datos por tenant (tablas, S3, Redis, Langfuse, LiteLLM, Celery) para borrado/offboarding, cubierto por test como el de tablas RLS | WS1.T04 | Offboarding de tenant de prueba deja 0 rastros; un almacén nuevo sin registrar hace fallar el test |

## WS8 — Demo y cierre

| ID | Task | deps | DoD |
|---|---|---|---|
| WS8.T01 | Guion de demo: 6 escenas (lookup federal, aislamiento NL vs JAL, refusal, toggling de idioma, adversarial, upload de expediente) | todo | Guion ejecutable en <15 min sin intervención manual |
| WS8.T02 | Corrida final de golden suite + reporte de métricas vs gates como anexo de la demo | WS5.T05 | Reporte adjunto; los 2 gates duros (citation=1.00, leakage=0.00) en verde |
| WS8.T03 | Retro técnica: deuda registrada como ADRs pendientes en `.claude/rules/architecture.md` | T02 | Lista priorizada de siguientes pasos post-demo |

---

## Secuencia crítica (camino mínimo a demo)

```
WS0 ──▶ WS1 ──▶ WS2.T01-T03,T05-T07 ──▶ WS3 ──▶ WS4 ──▶ WS5.T01-T04 ──▶ WS6.T01-T04 ──▶ WS8
                 (US puede diferirse            (WS7 corre en paralelo desde WS4)
                  a post-demo si aprieta)
```

Paralelizable desde el inicio: WS0.T05 (`Brand.md`) y WS4.T02–T03 (composer y templates no
dependen de datos reales — se desarrollan contra el sandbox).

Añadido 2026-11-02 (lecciones de Library-RAG): las tareas de medición (WS3.T03 líneas base,
WS3.T04 calibración de τ, WS5.T06 protocolo) preceden a cualquier optimización de recall o prompts.

---

## Log

| Fecha | WS | Cambio |
|---|---|---|
| 2026-11-06 | WS1 (+WS0.T02/T03) | WS1 completo: `docker-compose` (pgvector 0.8.0-pg16 + redis), 4 roles sin BYPASSRLS (`legalai_migrator`, `legalai_app`, `corpus_ingestor`, `legalai_provisioner`), Alembic 0001–0004 (catálogos MX/US, corpus con ltree/HNSW/tsvector con `unaccent`, RLS ENABLE+FORCE en 8 tablas, producto + `audit_log` append-only), `tenant_transaction` con `set_config(...,true)`, modelos SQLAlchemy. 66 tests contra Postgres real; 4 mutaciones de RLS verificadas (todas detectadas). Hallazgos: ver ADR-010/011. Pendiente de confirmar: ADR-010. |
| 2026-11-02 | — | Segunda pasada a Library-RAG (scripts, config, indexador, runner de eval, validador de citas, doctor, reset, tests/fakes). Añadido: WS0.T09 `doctor`, WS0.T10 bootstrap idempotente; DoDs de WS0.T06, WS2.T06, WS4.T01, WS5.T06; reglas de determinismo, plegado de acentos, validador puro, detección de refusals en prosa, settings validados, pin de modelos, run record/preflight/varianza de eval, contrato de corrida de ingesta; lecciones 31–42 en `docs/lessons-from-library-rag.md`. |
| 2026-11-02 | — | Revisión de Library-RAG (README, CLAUDE, DECISIONS, PLAN, reporte académico, skills). Incorporadas lecciones: invariantes 5–6 en CLAUDE.md, ADR-006…009 (Proposed), reglas de chunker/validador/retrieval ampliadas, skills `decision-record` y `legal-chunking`, `eval-runner` con protocolo antes/después y autoría de golden; tareas WS0.T06–T08, WS2.T08–T10, WS3.T07, WS5.T06–T07, WS6.T08, WS7.T05–T06; `docs/lessons-from-library-rag.md`. |
