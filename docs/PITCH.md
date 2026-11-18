# PITCH.md — Legal AI Assistant
### Investigación jurídica con IA donde cada afirmación es verificable — o no existe

---

## 1. El problema

Los abogados en México y EE.UU. enfrentan tres realidades simultáneas:

1. **La investigación jurídica es cara y lenta.** Localizar la norma aplicable a través de
   tres niveles de gobierno (federal, 32 estados, miles de municipios en MX), verificar su
   vigencia tras reformas constantes y cruzarla con jurisprudencia consume una fracción enorme
   de horas facturables — frecuentemente delegada a los perfiles con menos criterio para ella.
2. **La IA generativa genérica es inutilizable en este dominio.** Los LLMs de propósito
   general inventan artículos, mezclan legislación de estados distintos y citan tesis
   inexistentes con total fluidez. Los casos sancionados en cortes de EE.UU. por citas
   fabricadas (desde *Mata v. Avianca* en adelante) convirtieron "usé ChatGPT" en un riesgo
   de responsabilidad profesional, no en una ventaja.
3. **La confidencialidad no es negociable.** Los expedientes están protegidos por secreto
   profesional; subirlos a herramientas que no garantizan aislamiento estricto es una
   violación ética per se.

El mercado no necesita "un chatbot que sepa de leyes". Necesita un sistema donde **la
velocidad de la IA y el estándar probatorio de un abogado dejen de estar en conflicto**.

## 2. La solución

Un asistente jurídico bilingüe (es-MX / en-US), multi-jurisdiccional y multi-tenant, con un
contrato de producto inusual en la categoría:

> **Toda afirmación jurídica sale con una cita verificable a un fragmento recuperado del
> corpus oficial — o el sistema se rehúsa a responder y dice exactamente qué fuente falta.**

Dos flujos de trabajo sobre ese contrato:

- **Dossier Builder** — compila expedientes de investigación: marco normativo aplicable,
  criterios jurisprudenciales y análisis, con cada afirmación anclada a un `cite_id`
  clickeable que abre el PDF oficial en el párrafo exacto, jerarquizando fuentes
  (Constitución > ley federal > estatal > municipal; jurisprudencia obligatoria > tesis aislada).
- **Adversarial Counterpart** — actúa como abogado de la contraparte: ataca la estrategia del
  usuario con contradicciones normativas citadas y falacias lógicas señaladas, distinguiendo
  explícitamente derecho citado de razonamiento estratégico, y cerrando con las
  vulnerabilidades priorizadas del caso.

El usuario selecciona **jurisdicción** (país → estado → municipio: p. ej. México → Nuevo
León → Monterrey), **especialidad** (laboral, penal, corporativo/financiero) e **idioma**;
el sistema garantiza estructuralmente que jamás le contestará con la ley de otro estado.

## 3. Por qué se puede confiar (los diferenciadores no son features, son arquitectura)

**a) La negativa fundada es una feature, no un fallo.** Si el corpus recuperado no sustenta
la respuesta, el sistema emite un bloque estructurado: qué falta y qué acción lo resuelve.
Un asistente que sabe decir "no tengo la fuente" es el único que un abogado puede citar en
juicio sin miedo.

**b) El aislamiento jurisdiccional es matemático, no probabilístico.** El filtro por
estado/municipio ocurre como predicado SQL *antes* de cualquier búsqueda semántica — la ley
de Jalisco es literalmente invisible para una sesión de Nuevo León. No dependemos de que un
modelo "entienda" la diferencia.

**c) El secreto profesional vive en la base de datos.** Row-Level Security de PostgreSQL
aísla los expedientes de cada despacho por debajo del código de aplicación: un bug no puede
filtrar datos entre firmas. El aislamiento se demuestra con una prueba automática de fuga
cross-tenant en cada build, no se promete.

**d) Verificación posterior a la generación.** Un validador independiente del modelo revisa
cada cita emitida contra lo efectivamente recuperado en esa consulta; una cita no sustentada
se regenera o se convierte en negativa. La alucinación no se "minimiza" — se vuelve
estructuralmente inexpresable en la salida.

**e) Bilingüe de verdad.** Embeddings cross-lingual: preguntas en inglés recuperan derecho
mexicano en español y viceversa. Las citas textuales de normas se conservan siempre en su
idioma original; las traducciones se marcan como no oficiales.

## 4. Especificación de ingeniería

### 4.1 Stack

| Capa | Tecnología | Rol |
|---|---|---|
| Frontend | Next.js + TypeScript, next-intl | UI bilingüe con toggling sin recarga, streaming SSE, render de citas con deep-link |
| API | FastAPI (Python 3.12), gestionado con `uv` | Validación de sesión (jurisdicción/persona/idioma contra catálogos cerrados), middleware RLS |
| Datos | PostgreSQL 16 + `pgvector` (HNSW) + `tsvector` + `ltree` | Un solo perímetro: relacional, vectorial y full-text bajo las mismas políticas RLS |
| Parsing | Docling (IBM, open-source) | PDFs legales multi-columna → estructura jerárquica navegable; auditable y sin SaaS externo |
| Normalización | Gramáticas jurídicas propias por tipo de fuente | Artículo/Fracción/Inciso (MX), §/subsection/holding (US) → árbol `ltree` con vigencia |
| Embeddings | Cohere embed-v3 multilingual | Espacio vectorial cross-lingual compartido es/en |
| Reranking | Cross-encoder multilingüe (Cohere Rerank 3 / bge-reranker self-hosted) | Precisión fina sobre candidatos híbridos |
| Orquestación LLM | LiteLLM Proxy (self-hosted) | Routing por alias de tarea sobre Anthropic / OpenAI / Gemini / DeepSeek; budgets y límites por tenant; fallbacks |
| Async | Celery + Redis | Ingesta pesada fuera del camino de consulta |
| Observabilidad | Langfuse + Prometheus; audit log inmutable | Cada respuesta reconstruible: prompt, fragmentos, veredicto del validador |

### 4.2 Pipeline de una consulta

```
sesión {tenant, idioma, jurisdicción, persona, workflow}
  → predicado jurisdiccional duro (SQL, apilamiento federal→estatal→municipal, jamás laterales)
  → búsqueda híbrida prefiltrada: brazo semántico (coseno) + brazo léxico (FTS)
  → fusión RRF → rerank cross-encoder → umbral de confianza τ
       └─ bajo τ: NEGATIVA ESTRUCTURADA sin invocar el LLM
  → expansión al artículo padre (ltree) → presupuesto de contexto etiquetado con cite_id
  → prompt compuesto en 5 capas versionadas (núcleo anti-alucinación fijo → workflow →
    persona → sesión → fragmentos; ninguna variable proviene de texto libre del usuario)
  → LLM vía alias de tarea (LiteLLM)
  → VALIDADOR DE CITAS: existencia, cobertura y cita textual contra lo recuperado; 1
    regeneración; si persiste, negativa (generada por código, no por el modelo)
  → audit log → respuesta con citas clickeables
```

### 4.3 Datos y multi-tenancy

- Cada fragmento recuperable porta su genealogía jurídica como columnas tipadas e indexadas
  (`country / level / state_code / municipality / doc_type / is_current`) y un **`cite_id`
  inmutable y versionado** (`MX-FED-LFT-ART47-FRACII-v2025.06`) — el contrato entre modelo,
  validador y UI.
- El chunking respeta la estructura legal (unidad de cita = fracción/párrafo; unidad de
  contexto = artículo íntegro), con la jerarquía embebida en el texto antes de vectorizar.
- Modelo pooled multi-tenant con RLS forzado (`FORCE ROW LEVEL SECURITY`) y tenant sintético
  `PUBLIC_CORPUS` para el derecho público compartido; expedientes privados bajo el tenant del
  despacho. Storage con prefijos y políticas IAM espejo; presupuestos LLM por tenant.
- Las reformas versionan, nunca borran: consultas históricas son un modo explícito con
  advertencia de vigencia.

### 4.4 Calidad medida, no declarada

Suite dorada (golden dataset) con distractores deliberados — el mismo número de artículo en
otro estado, la versión derogada de la norma — corriendo en CI ante cualquier cambio de
prompt, chunker, embeddings o modelo:

| Métrica | Gate de release |
|---|---|
| Precisión de citas (citas emitidas que existen en lo recuperado) | **= 1.00, duro** |
| Fuga jurisdiccional | **= 0.00, duro** |
| Fidelidad de afirmaciones al fragmento citado | ≥ 0.98 |
| Exactitud de negativas (rehúsa cuando debe) | ≥ 0.95 |
| Falsas negativas (rehúsa cuando podía responder) | ≤ 0.05 |
| Recall de recuperación @k | ≥ 0.90 |

Los modelos compiten por los aliases de tarea con estas métricas; el proveedor detrás de cada
alias puede cambiar sin tocar código de aplicación.

Cómo se mantiene honesta la medición: los casos los redactan o aprueban abogados (cada caso
declara autoría y revisión); las diferencias de 1–2 casos se reportan como ruido, no como
mejora; cada corrida guarda su configuración y versiones; la fidelidad se califica sobre hojas
que muestran el texto completo de cada fragmento citado; y todo cambio de recuperación o prompts
registra cifras antes y después.

### 4.5 Honestidad de alcance

El sistema garantiza **trazabilidad, no criterio jurídico**: es una herramienta para
abogados, no un abogado. Una cita válida prueba procedencia, no que la paráfrasis sea fiel
(cita correcta con resumen invertido es un riesgo residual conocido, mitigado con cita textual,
NLI y revisión humana). Los gates se calculan sobre conjuntos pequeños al inicio: una diferencia
de uno o dos casos es ruido. Su calidad depende de la frescura del corpus (la vigilancia del DOF
y gacetas es trabajo operativo permanente) y su costo por consulta es ~1.3–1.6× el de un RAG
ingenuo por el doble paso de validación — en este dominio, ese sobrecosto *es* el producto.

## 5. Estado y camino

- **Hoy (2026-11-14):** demo funcional que corre completa en un equipo, sin enviar nada fuera
  (`docs/LOCAL_DEMO.md`): tres espacios de trabajo (Conversar, Investigación, Argumentar) con citas
  verificadas y negativa estructurada; navegador de leyes, verificador de citas de escritos, asuntos con
  miembros, vigilante de reformas y **documentos del propio despacho** citables y aislados por asunto.
  Medido sobre 25 casos redactados por IA, aún sin revisión de abogados: recall@8 1.00, fuga
  jurisdiccional 0, negativa correcta 5/5, falsa negativa 1/20 con el modelo local. Diseño en
  `docs/LegalAI-System-Architecture.md`, decisiones en el registro de ADR, avance en `PLAN.md`.
- **Caso completo listo para revisión (2026-11-17):** un despido laboral ficticio corrido por todas las
  herramientas, con un paquete para que un abogado califique cada respuesta contra el texto completo de lo citado
  (`docs/EVALUACION-E2E.md`). La primera corrida ya mostró lo que solo un abogado puede juzgar: citas reales y
  verificadas a artículos que no rigen el caso.
- **Falta para cerrar la demo:** revisión del conjunto de evaluación por abogados (hasta entonces
  las compuertas no cuentan), OCR para documentos escaneados y medir el perfil remoto opcional.
- **Después:** calculadora de términos procesales, plantillas de escritos, jurisprudencia y el
  citator mexicano, exportación del dossier (`ROADMAP.md`). El monitoreo de reformas y el
  verificador de citas de escritos externos, antes en esta lista, ya están construidos.

---

*From Pipeline to Decision.* — OPB
