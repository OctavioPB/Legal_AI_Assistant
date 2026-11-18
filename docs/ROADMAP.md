# ROADMAP.md — Legal AI Assistant: implementaciones futuras

Todo lo listado aquí está **fuera del scope de la demo** (ver PLAN.md) pero fue considerado
en el diseño: la arquitectura actual (perímetro Postgres/RLS, `cite_id` inmutable, árbol
`ltree`, aliases de modelo, golden dataset) es la base sobre la que cada ítem se apoya.
Cada feature indica qué pieza existente lo habilita, para evitar rediseños.

Criterio de ordenamiento: valor para el abogado practicante ÷ esfuerzo, con el candado de
que nada entra si degrada los dos gates duros (citation precision = 1.00, leakage = 0.00).

---

## Horizonte 1 — Post-demo inmediato (0–6 meses): profundizar el flujo diario

**R1.1 · Monitoreo de reformas (DOF + gacetas estatales) con alertas por dossier.** *(Núcleo hecho 2026-11-08,
WS9.T04/ADR-017: vigilante de las URL oficiales de diputados.gob.mx + bandeja local, linaje por unidad citable y
alertas por conversación/fuente/reporte. Falta: DOF diario y gacetas estatales como fuentes, notificación fuera de
la app.)*
Pipeline programado que detecta publicaciones nuevas, las ingesta y —clave— cruza los
`cite_id` reformados contra los dossiers activos de cada tenant: *"El art. 76 LFT citado en
tu expediente X fue reformado el 14-06; la versión que citaste ya no está vigente"*. Es la
feature de mayor retención posible para un abogado: convierte el corpus fresco (hoy un riesgo
operativo, ver Limitaciones §12 del blueprint) en un producto. Habilitado por: versionado
`is_current` + `cite_id` inmutable ya existentes.

**R1.2 · Calculadora de términos procesales por jurisdicción.**
Plazos (contestación, apelación, ofrecimiento de pruebas) calculados desde el código procesal
de la jurisdicción activa, con calendario de días inhábiles por tribunal. Determinista, no
generativo — pero cada plazo se muestra con su `[cite:]` al artículo procesal. Los términos
son la causa #1 de responsabilidad profesional; una calculadora citable es defendible ante
el cliente y el colegio.

**R1.3 · Verificador de citas de documentos externos.** *(Hecho para leyes 2026-11-08, WS9.T02/ADR-019;
jurisprudencia y tesis pendientes de corpus.)*
Subir el escrito de la contraparte (o el propio antes de presentarlo) y validar cada cita
normativa/jurisprudencial contra el corpus: existe, está vigente, dice lo que se le atribuye.
Es el pipeline de validación actual (WS5) apuntado hacia adentro de un PDF ajeno — reuso casi
directo del citation validator + retrieval. Caso de uso post-*Mata v. Avianca* con demanda
obvia: detectar alucinaciones de la IA del contrario.

**R1.4 · Plantillas de escritos por materia y tribunal (drafting asistido).**
Demanda laboral, contestación, amparo indirecto: esqueletos por persona/jurisdicción donde el
Dossier Builder puebla el capítulo de derecho con citas verificadas y el abogado redacta
hechos. Estrictamente asistivo: el sistema nunca "presenta", solo pre-llena con trazabilidad.

**R1.5 · Colaboración por asunto (matter-level).** *(Primer paso hecho 2026-11-08, WS9.T03/ADR-018: asuntos
con miembros bajo RLS, notas y fuentes guardadas. 2026-11-14, ADR-025: documentos del despacho por asunto, con
el sub-scoping de RLS por matter que este punto pedía. Faltan papeles socio/asociado/pasante, comentarios sobre
fragmentos y versiones del dossier.)*
Permisos por expediente dentro del tenant (socio/asociado/pasante), comentarios sobre
fragmentos citados, historial de versiones del dossier. Habilitado por: `dossiers` +
`audit_log` existentes; requiere sub-scoping de RLS por matter (política adicional, no
rediseño).

**R1.6 · Backlog de lagunas del corpus a partir de los refusals.**
Cada `<insuficiente>` por falta de fuente se agrega en un reporte de "temas pedidos que el
corpus no cubre" (deduplicado por tema, con conteo y jurisdicción), que alimenta la cola de
ingesta y el monitoreo de R1.1. Convierte la negativa en señal de producto. Lección de
Library-RAG (detector de lagunas): los embeddings solo preseleccionan temas parecidos (coseno
0.41–0.81 para el mismo tema reformulado vs 0.31–0.73 para temas vecinos); la fusión de temas la
decide un paso aparte, no el coseno. Habilitado por: `audit_log` + payload estructurado del refusal.

## Horizonte 2 — Consolidación (6–18 meses): inteligencia sobre el corpus

**R2.1 · Citator mexicano (grafo de vigencia y contradicción).**
Grafo dirigido sobre `cite_id`s: qué tesis interrumpe/sustituye a cuál, qué jurisprudencia
por contradicción resolvió qué criterios encontrados, qué artículos remiten a cuáles
("salvo lo dispuesto en..."). Equivalente funcional de Shepard's/KeyCite, inexistente en
México a este nivel. Habilitado por: `doc_nodes` + detección de reformas del normalizador;
requiere extracción de relaciones (tarea LLM `cheap-utility` en batch) y una tabla de aristas.
Es probablemente el foso competitivo más profundo de todo el roadmap.

**R2.2 · Knowledge graph de remisiones normativas.**
Navegación "este artículo remite a estos 4; es remitido por estos 12" y expansión automática
del contexto de retrieval con remisiones de primer grado (hoy el parent expansion solo sube
en el árbol; esto expande lateralmente). Mejora directa de recall en preguntas complejas.

**R2.3 · Analytics de criterios por órgano jurisdiccional.**
Tendencias de resolución por tribunal colegiado/sala sobre el corpus público de sentencias:
qué criterios aplica cada órgano, con qué frecuencia concede amparos por materia. Restricción
ética explícita: agregados por órgano, nunca perfiles de juzgadores individuales orientados a
forum shopping — la línea se documenta en Brand.md/términos de uso.

**R2.4 · Búsqueda sobre audiencias y constancias (ASR).**
Transcripción de audiencias (con consentimiento/legalidad local) → ingesta como `client_file`
(la carga de documentos del despacho ya existe, ADR-025; falta ASR y la gramática de turnos)
con timestamps citables. El chunker jerárquico gana una gramática nueva (turnos de habla);
todo lo demás (RLS, citas, validador) aplica sin cambios.

**R2.5 · Expansión de jurisdicciones US + integraciones de expediente.**
Cobertura estatal US más allá de CA; conectores de solo-lectura a Tribunal Virtual (MX) y
PACER/CM-ECF (US) para traer constancias al matter automáticamente.

## Horizonte 3 — Plataforma (18+ meses)

**R3.1 · Tier enterprise: aislamiento físico y llaves propias.**
Schema-per-tenant o DB-per-tenant + BYOK (envelope encryption por tenant) + residencia de
datos configurable. La migración está pre-cableada: `tenant_id` viaja en todas las tablas
(ADR-002).

**R3.2 · Despliegue on-prem / VPC del despacho.**
Para despachos con prohibición de nube compartida. Requiere que el reranker y embeddings
tengan variantes self-hosted (ya contemplado: `bge-reranker-v2-m3` como alternativa) y un
alias de modelo local (p. ej. DeepSeek self-hosted) en LiteLLM.

**R3.3 · Modelo propio legal-es (destilación).**
Con el volumen de trazas Langfuse + golden dataset ampliado, destilar un modelo pequeño para
`cheap-utility` (clasificación, extracción de relaciones del citator) reduce costo del doble
paso de validación. Nunca para drafting hasta que iguale citation precision del alias actual
— el router lo decide con datos, no la estrategia.

**R3.4 · Expansión LATAM (Colombia, Chile, Argentina).**
El diseño jurisdiccional (país→nivel→entidad, catálogo cerrado, apilamiento hacia arriba) es
generalizable a otros sistemas civilistas; el costo real es el normalizador por país (gramática
nueva) y el corpus. Entrar por países con diario oficial digitalizado y estructurado.

**R3.5 · API pública + marketplace de conectores.**
Exponer retrieval citable y verificación de citas como API para software de gestión de
despachos (practice management). El citation validator como servicio es vendible por sí solo.

---

## Anti-roadmap (decisiones de NO hacer, para no relitigar)

- **No** chatbot legal para consumidores finales: cambia el perfil de riesgo (asesoría no
  autorizada) y el diseño entero asume un profesional que valida.
- **No** "predicción de resultados de juicio" como feature: promesa indefendible con esta
  evidencia; los analytics de R2.3 se quedan en frecuencias descriptivas.
- **No** map-reduce / resumen por documento como estrategia de síntesis por defecto: medido en
  Library-RAG, bajó la fidelidad (0.87 → 0.58) y dejó de abstenerse (ADR-008). Se reabre solo con
  una corrida golden que gane en fidelidad Y exactitud de negativas.
- **No** fusión igualitaria de consultas reescritas/traducidas con la original (ADR-009).
- **No** juez LLM sin calibrar como único verificador de fidelidad: el verificador es código más
  revisión humana; un juez debe medir su acuerdo con abogados antes de bloquear nada.
- **No** dejar que el modelo redacte el refusal ni que "explique lo que falta" con sus palabras.
- **No** relajar el refusal para "parecer más útil": el `<insuficiente>` es contrato de
  producto (invariante #4 de CLAUDE.md).
