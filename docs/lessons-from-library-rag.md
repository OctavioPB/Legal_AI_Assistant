# Lecciones de Library-RAG aplicadas a Legal AI Assistant

Fuente: `C:\Users\USER\Documents\Github\Library-RAG` (README, CLAUDE, DECISIONS —64 ADR—, PLAN,
`docs/reporte-academico.md`, skills). Revisión del 2026-11-02.

Library-RAG es un RAG 100 % local sobre una biblioteca personal de Markdown, con el mismo contrato
de fondo que este proyecto: todo se cita, y si no hay evidencia se dice. Sus cifras provienen de un
corpus, un usuario y modelos locales pequeños (Qwen3-4B, Gemma 4 26B-A4B); **no se trasladan como
números, se trasladan como hipótesis a medir aquí** con la golden suite.

## Cómo leer la tabla
*Evidencia* = lo medido allá (ADR/sección del reporte). *Aplicación* = dónde quedó reflejado aquí.
*Estado*: **Regla** (ya en reglas/skills), **ADR propuesto** (decisión pendiente de medir),
**Tarea** (en PLAN).

| # | Lección | Evidencia en Library-RAG | Aplicación aquí | Estado |
|---|---|---|---|---|
| 1 | Medir antes de adoptar; conservar solo lo que mejora | §8.1: cuatro ideas plausibles rechazadas al medirlas | CLAUDE.md "Lessons", `eval-runner` (líneas base por brazo), WS3.T03 | Regla / Tarea |
| 2 | Map-reduce como síntesis por defecto empeora fidelidad y abstención | ADR-017: fidelidad 0.87 → 0.58, abstención 2/3 → 0/3, 4× latencia | ADR-008, anti-roadmap | ADR propuesto |
| 3 | Fusión igualitaria de consultas reescritas/traducidas baja el recall | ADR-038, 052: −6 pts recall@8; síntesis 0.54 → 0.43–0.48 | ADR-009, WS3.T06, `jurisdictions.md` | ADR propuesto |
| 4 | La abstención debe ser acción del sistema, no instrucción al modelo | §7.4: v1 2/5 → v2 5/5 al devolver frase fija sin generar | Invariante 6, ADR-007, WS5.T02 | Regla / ADR propuesto |
| 5 | Calibrar el umbral de cobertura a mano con sets cubiertos/ausentes y varias formulaciones | ADR-056: ≥ 0.76 vs ≤ 0.15; sinónimos 0.06 vs 0.82 | ADR-007, WS3.T04 | Tarea |
| 6 | Verificación determinista: cita literal con pliegue de acentos y racha mínima; marcadores huérfanos | §3.18, §8.2: 46 % → 80 % de citas literales con modelo mayor; `[n]` copiado del prompt | `rag.md` validador, WS5.T01–T02, `prompts.md` | Regla / Tarea |
| 7 | Cita válida ≠ paráfrasis fiel (riesgo residual declarado) | ADR-058, §8.2 | Blueprint §10, §12; PITCH §4.5 | Regla |
| 8 | No ordenar al modelo "añade un detalle / di qué falta": inventa con cita falsa | CLAUDE.md "Tutor levels"; ADR-032 | `prompts.md`, `prompt-lab` | Regla |
| 9 | Guardas de versión: no mezclar vectores/chunks; guardar umbrales de chunking; reconstruir aparte y cambiar atómicamente | CLAUDE.md regla 6, "Index mechanics" | Invariante 5, ADR-006, WS2.T08 | Regla / ADR propuesto |
| 10 | La ventana del embedder trunca en silencio (77 chunks > 1 024 tokens) | Plan fase 4 | `rag.md`, WS2.T05, blueprint §6.2 | Tarea |
| 11 | Los índices/tablas de contenido compiten con las secciones; el peso del título en BM25 favorece notas grandes y se barre con evidencia | ADR-044: peso 0.5, 12 mejoran, 0 empeoran | `rag.md`, WS3.T07 | Tarea |
| 12 | Lead-ins (“…se calcula como:”) viajan con lo que introducen | skill `markdown-chunking` | Caput glue: `rag.md`, `legal-chunking`, WS2.T10 (a medir) | Tarea |
| 13 | `text` = slice exacto de la fuente; breadcrumb solo como metadato/prefijo | skill `markdown-chunking` | `rag.md`, `legal-chunking` | Regla |
| 14 | Un bug de parseo = primero una fixture; fixtures patológicas + property tests de cobertura | CLAUDE.md "Conventions"; 17–19 fixtures | `rag.md`, WS2.T09 | Tarea |
| 15 | El set de evaluación lo escribe el usuario; lo redactado por la IA se rotula y se mide su sesgo | skill `retrieval-eval`; §8.6, §9; 0.43 → 0.81 | `eval-runner` (autoría), WS5.T05 | Regla / Tarea |
| 16 | Las alternativas válidas las juzga el humano; el error puede estar en la referencia | `biblio candidates`; §7.6 | `eval-runner` | Regla |
| 17 | Ruido de sets pequeños: 1–2 casos no son mejora; regresión > 2 pts por tipo bloquea | skill `retrieval-eval` | CLAUDE.md, `eval-runner`, WS5.T06 | Regla / Tarea |
| 18 | Las herramientas de evaluación fallan (regex que no leía calificaciones; hojas truncadas a 400 chars) | §8.4 | `eval-runner` "hojas de revisión", WS5.T07 | Tarea |
| 19 | Métrica 1.00 "por construcción" no mide lo que parece | §6.6 (hijo atado a su rama: 1.00; juzgado a mano 24–30 %) | `eval-runner` "Interpretando fallos" | Regla |
| 20 | Registrar cada corrida (commit, config efectiva) append-only | skill `retrieval-eval` | `eval-runner` | Regla |
| 21 | Vigilar los tripwires de los ADR (se superó 200 k → ≈697 k chunks sin notarlo) | §9 validez externa; ADR-064 | `decision-record`, WS7.T05 | Tarea |
| 22 | ADR con estados, "Pending" y "propón antes de implementar"; superseder, no editar; avisar si el código contradice un ADR | skill `decision-record` | `architecture.md`, skill `decision-record`, WS0.T08 | Regla |
| 23 | Config sin valores mágicos; `Protocol` para Embedder/Reranker/LLMClient | CLAUDE.md "Conventions" | `architecture.md`, WS0.T06 | Tarea |
| 24 | Tests con red bloqueada; garantías por capas | ADR-002; `pytest-socket` | `style.md`, WS0.T07 | Tarea |
| 25 | UI: sin recursos externos, texto del modelo como texto, i18n por test, token de contraste | CLAUDE.md "web UI loads nothing", ADR-041/046 | `style.md`, `Brand.md`, WS6.T08 | Tarea |
| 26 | Un almacén nuevo de datos debe registrarse en el borrado (`reset.memory_items`) | ADR-040 | `multi-tenancy.md`, WS7.T06 | Tarea |
| 27 | Planes de conversación: el primer mensaje se busca tal cual; la reescritura nunca cambia el alcance | ADR-038 | ADR-009, `jurisdictions.md` | ADR propuesto |
| 28 | Las lagunas detectadas (abstenciones) se vuelven backlog | ADR-029, 030, 047 | ROADMAP R1.6 | Futuro |
| 29 | Los embeddings solo preseleccionan; para decidir "mismo tema" no basta el coseno | ADR-030 | ROADMAP R1.6 | Futuro |
| 30 | La memoria/costo real no es el teórico: observar el crecimiento por petición, no solo el pico | §8.3 | Pendiente: aplicar a presupuestos LiteLLM por tenant y latencia p95 bajo carga (WS7.T03) | Observación |
| 31 | Los invariantes deben ser ejecutables: `doctor` con OK/WARN/FAIL (extensión carga, FTS pliega acentos, digest del modelo, contexto suficiente) | `doctor.py` (checks de sqlite-vec, FTS5 `remove_diacritics`, digests, contexto servido) | WS0.T09, CLAUDE.md | Tarea |
| 32 | Lanzador de un paso, idempotente, que pregunta antes de descargar | `scripts/start.sh`, ADR-021 | WS0.T10 | Tarea |
| 33 | Registro de corrida completo: `commit` + `dirty`, config efectiva, ranks por pregunta, p50/p95, y preflight que aborta si el set apunta a algo inexistente | `eval/runner.py` (`check_expected`, `_git_state`, `per_question`) | `eval-runner` (run record, preflight), WS5.T06 | Regla / Tarea |
| 34 | Una corrida de ingesta: lock con PID y toma de control si el dueño murió, transacción por documento, `failed` sin abortar, estadísticas, embedder perezoso | `index/indexer.py` (`_index_lock`, `IndexStats`, `_Lazy`) | `legal-chunking` "Ingestion run contract", WS2.T06 | Regla / Tarea |
| 35 | Reconstrucción completa en archivo aparte y swap solo si termina bien; diferencias de `index_meta` listadas en el error | `indexer.py` (`meta_differences`, `--full` → `.tmp`) | ADR-006, WS2.T08 | ADR propuesto |
| 36 | Validador de citas = función pura con resultado inmutable; parser de grupos `[1, 3]`; dedupe en orden; abstención tolerante vía "simplify" | `generate/citations.py` | `rag.md` validador, WS5.T01 | Regla |
| 37 | Desempates estables en RRF; la fusión usa solo rangos | `retrieve/fusion.py` | `rag.md` determinismo | Regla |
| 38 | Settings tipados y congelados, con cada presupuesto de salida del LLM y cada umbral comentado con su ADR y la cifra medida; restricciones cruzadas (contexto + respuesta ≤ ventana) | `config/default.toml`, `config.py` | `architecture.md`, WS0.T06 | Regla / Tarea |
| 39 | Modelos fijados por revisión/digest verificados en su ficha, no de memoria | CLAUDE.md "Do not trust training memory"; `[llm] model_digest` | `architecture.md` "Pin, don't trust memory", WS4.T01 | Regla / Tarea |
| 40 | Arquitectura de pruebas: fakes compartidos deterministas, fixture de config que apaga lo caro, marcador para pruebas con modelos reales, `mypy --strict` + ruff | `tests/fakes.py`, `tests/conftest.py`, `pyproject.toml` | `style.md` | Regla |
| 41 | El plegado de acentos del brazo léxico es un requisito probado, no un detalle | CLAUDE.md "FTS5 and accents" | `rag.md`, `doctor` | Regla |
| 42 | Un reset/borrado se niega a tocar rutas protegidas sin importar la config (`check_safe`) | `reset.py` | `multi-tenancy.md` (borrado nunca toca `PUBLIC_CORPUS`), WS7.T06 | Regla |

**Observación sobre la fuente (para quien lea Library-RAG):** el reporte (§8.4) dice que las hojas
de revisión ya muestran el texto completo del fragmento citado, pero `generate/answer_eval.py`
sigue acotando el extracto con `_EXCERPT_CHARS = 400`. Aquí se exige el texto completo con un test
(WS5.T07); conviene revisar si el hallazgo se corrigió en otra ruta o quedó pendiente allá.

## Qué NO se importó (y por qué)
- **Garantía offline / un solo comando con red:** este es un SaaS con proveedores externos; el
  equivalente aplicable es el control de egreso en tests (lección 24) y la política de datos por
  tenant, no "cero red".
- **Presupuesto de memoria < 8 GB, Ollama, cuantización, modelo en RAM:** no aplica a modelos
  alojados por LiteLLM; el análogo es el presupuesto por tenant y la latencia p95.
- **SQLite + sqlite-vec y búsqueda exacta:** Postgres/pgvector/RLS es el perímetro (ADR-001); la
  lección transferible es solo vigilar el tripwire (21).
- **Repetición espaciada, mapas conceptuales, "Explícamelo tú", lector integrado:** funciones de
  estudio fuera del alcance de un producto para abogados en ejercicio (anti-roadmap: no es un
  tutor). Su técnica de verificación (cita literal) sí se importó.
- **Modo "Razonar" con capa de conocimiento general del modelo:** choca con el contrato de este
  proyecto (el LLM solo afirma contenido jurídico recuperado). Se conserva únicamente el
  etiquetado de lo que añade el modelo (`[razonamiento estratégico]`), que ya existía.

## Cómo mantener este documento
Cuando una lección se mida aquí y cambie el veredicto, **no se edita la fila**: se añade una nueva
con la fecha y el ADR/corrida que la sustituye (mismo criterio que los ADR).
