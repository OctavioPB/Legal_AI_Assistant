# Casos de extremo a extremo (E2E) para revisión de abogados

Un caso E2E recorre la aplicación completa como lo haría un abogado, con los modelos reales, y produce un paquete
para que **un abogado real** juzgue cada salida. No es la suite golden: aquí nada se califica solo. El sistema
reporta lo que él mismo verificó (procedencia de las citas, citas textuales, cobertura); la exactitud jurídica, la
fidelidad a las fuentes y la utilidad son la calificación del abogado.

## Casos

| Id | Carpeta | Materia | Estado |
|---|---|---|---|
| E2E-LAB-01 | `laboral_01/` | Despido verbal de una trabajadora embarazada (LFT, CPEUM 123-A) | Corrido 2026-11-17; esperando revisión |

Cada caso tiene `caso.md` (hechos), `guion.json` (preguntas, puntos y artículos esperados, referencias sembradas en
el escrito) y los documentos del caso. Todo es **ficticio** y lo redactó el equipo técnico con IA
(`authored_by: ai-draft`, `reviewed_by: null`): los «puntos esperados» solo valen como referencia cuando un abogado
los aprueba o los corrige.

## Correr un caso

```bash
docker compose up -d postgres redis
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000          # API con los modelos locales (Ollama)
uv run python -m evals.e2e.run_case laboral_01 --credentials .demo-credentials [--keep]
uv run python -m evals.e2e.review evals/e2e/runs/E2E-LAB-01-<UTC>.json
```

`run_case` crea el asunto, sus notas y su documento, hace cada pregunta, la investigación, las rondas de
Argumentar, el verificador y la hoja de puntos clave. Guarda en `runs/<id>-<UTC>.json` cada respuesta con los intentos y
veredictos del validador y el **texto completo** de cada unidad citada y de su artículo. Con `--keep` el asunto
queda en la aplicación para recorrerlo en pantalla. Con el modelo local tarda de 15 a 25 minutos.

`review` escribe junto a la corrida:

- `-revision.md`: el paquete completo en texto (para imprimir o anexar), con casillas de calificación;
- `-review.html`: la página interactiva que se publica para el abogado; sus calificaciones se guardan solas, una
  revisión por persona;
- `-review.json`: los mismos datos.

## Cómo se comparte con el abogado

La página se publica como artefacto privado. Para que el abogado pueda **guardar** sus calificaciones, compártala
desde el menú Compartir con acceso de **Colaborador** (si es del despacho) o invítelo **por correo como Editor** (si
es externo, y sin enlace público). Con acceso de solo lectura la página funciona, pero conserva las calificaciones
solo en su navegador y le ofrece «Copiar mis calificaciones» para enviarlas por correo. Las revisiones recibidas se
leen en la misma página (panel «Revisiones recibidas», visible para el dueño) o con la herramienta de datos del
artefacto (colección `reviews`).

## Qué hacer con la revisión

1. Los puntos esperados que el abogado apruebe o corrija se vuelven casos golden (`evals/golden/`), con
   `authored_by: ai-draft` y `reviewed_by: <abogado>`.
2. Cada «No» en «¿Esta fuente dice lo que la respuesta le atribuye?» es un error de fidelidad: se registra con su
   cita y se usa para medir la mejora (el validador prueba procedencia, no fidelidad; blueprint §12).
3. Los resultados se anotan en `docs/PLAN.md` con la fecha y el id de la corrida.
