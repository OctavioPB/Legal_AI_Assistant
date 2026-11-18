# Evaluación de extremo a extremo con revisión de abogado

La suite golden mide piezas sueltas (recuperación, citas, rechazos) con casos cortos. Una evaluación de extremo a
extremo (E2E) recorre **la aplicación completa sobre un asunto**, como lo haría un abogado, y deja que **un abogado
real** juzgue cada salida. Las calificaciones que importan aquí son la exactitud jurídica y la fidelidad de cada
cita; el sistema solo reporta lo que él mismo verificó.

Herramientas: `evals/e2e/run_case.py` (corre el caso contra la API) y `evals/e2e/review.py` (arma el paquete de
revisión). Cómo repetirlo: `evals/e2e/README.md`.

## Caso E2E-LAB-01 · Despido de una trabajadora embarazada

> **Caso ficticio.** Lo redactó el equipo técnico con ayuda de IA (`authored_by: ai-draft`, `reviewed_by: null`).
> Los «puntos esperados» son un borrador: valen como referencia solo cuando un abogado los aprueba o corrige.

**Martínez Solís vs. Distribuidora Ficticia del Norte, S.A. de C.V.** Representamos a la trabajadora. Auxiliar de
facturación desde el 1 de marzo de 2019, salario diario de $650.00, jornada real de 54 horas contra 45 pactadas.
Embarazada desde agosto de 2026, lo informó con constancia del IMSS. El 3 de octubre de 2026 la despidieron de
palabra por supuesta alteración de facturas, sin aviso escrito. No le pagaron vacaciones, prima vacacional ni
aguinaldo proporcional. Ley aplicable: LFT y CPEUM 123-A, versiones cargadas al 17-11-2026.

Material: `evals/e2e/laboral_01/` (`caso.md`, `guion.json`, `contrato.txt`, `escrito.txt`).

### Qué se ejecutó

| Herramienta | Qué se pidió | Qué pone a prueba |
|---|---|---|
| Asunto y documentos | Asunto con notas; contrato individual subido al asunto (11 partes citables) | Documentos del despacho (ADR-025) |
| Conversar C1–C6 | Rescisión por probidad y aviso; plazo para demandar; protección por embarazo; vacaciones tras la reforma de 2023; conciliación prejudicial y su excepción; lo que dice el contrato | Recuperación, citas, cobertura |
| Investigación I1 | Prestaciones e indemnizaciones por despido injustificado | Expediente largo con muchas normas |
| Argumentar A1, A2 | Reforzar nuestro argumento; ataque de la contraparte | Marcas de razonamiento estratégico |
| Verificar | Extracto de demanda con 9 referencias, algunas erróneas a propósito | Existencia, vigencia, cita textual, sin modelo |
| Hoja de puntos clave | Hoja de una página del asunto | ADR-028 |

### Resultados de la corrida

Corrida `evals/e2e/runs/E2E-LAB-01-20261117T225129Z` · 17-11-2026 · modelo local `gemma4:26b-a4b-it-qat@2dd70431afed`
· 17 minutos. El asunto quedó en la demo para recorrerlo en pantalla.

| Paso | Resultado | Segundos | Intentos | Fuentes citadas |
|---|---|---|---|---|
| C1 rescisión y aviso | Respondió | 104 | 2 | LFT 51; 47-II; 47-XV |
| C2 plazo para demandar | Respondió | 76 | 1 | LFT 48; 519; 47-XV |
| C3 embarazo | Respondió | 79 | 1 | LFT 133-XV; 283 Ter; CPEUM 123-A-V |
| C4 vacaciones | Respondió | 78 | 1 | LFT 76; 79; contrato, cláusula SÉPTIMA |
| C5 conciliación | Respondió | 52 | 1 | LFT 684-B; 685 Ter |
| C6 el contrato | Rechazó (umbral) | 6 | 0 | — |
| I1 prestaciones | Respondió | 67 | 1 | LFT 436; 162; 87; 79; 50 |
| A1 reforzar | Respondió | 108 | 2 | LFT 47; 341; 133-XV |
| A2 ataque | Respondió | 108 | 2 | LFT 47; 47-II; 47-XV |
| Hoja H1 | Rechazó (validador) | 92 | 2 | — |
| Hoja H2, regenerada | Rechazó (validador) | 55 | 2 | — |

**Verificador** (9 referencias):

| Referencia del escrito | Esperado | Resultado |
|---|---|---|
| LFT 47, cita sobre la falta de aviso | Vigente; cita coincide | Vigente; coincide |
| LFT 48, cita sobre reinstalación o indemnización | Vigente; cita no coincide* | Vigente; no coincide |
| LFT 76, cita con «seis días» (texto anterior a 2023) | Vigente; cita no coincide | Vigente; no coincide |
| LFT 162 y 68 | Vigentes | Vigentes |
| LFT 133-XV y CPEUM 123-A-XXII | Vigentes | Vigentes |
| Ley del Seguro Social, art. 15 | Fuera del acervo | Fuera del acervo |
| LFT 47 Bis 9 | No existe | No existe (reportada como «art. 47BIS») |

\* El esperado original decía «coincide». Ver el hallazgo 2.

### Hallazgos

1. **La pregunta sobre el contrato no se respondió.** La mejor puntuación fue 0.571 contra un umbral de 0.58, y las
   cláusulas del contrato quedaron en 0.546 y 0.541. El umbral se calibró con texto de leyes, no con prosa
   contractual (pendiente ya anotado en ADR-025). En C4 el contrato sí se citó, porque la pregunta también tocaba
   la ley. Siguiente paso: un conjunto de sondeo con documentos del despacho y recalibrar el umbral.
2. **El verificador encontró un error nuestro.** La cita del art. 48 en el escrito la copiamos de un texto anterior
   a la reforma de 2019. El vigente dice «ante la Autoridad Conciliadora, o ante el Tribunal si no existe arreglo
   conciliatorio». El sistema la marcó como no coincidente. Corregimos el esperado en `guion.json` después de leer
   el texto vigente, y queda registrado así.
3. **La hoja de puntos clave no se entregó en este asunto.** Fueron 4 intentos, todos rechazados por el validador:
   el modelo citó unidades que no se le mostraron (fracciones del art. 162, el art. 436), con o sin sufijo de
   versión. El validador hizo lo correcto. En un asunto chico la hoja sí se entrega (2 de 3, PLAN 17-11-2026), pero
   con 19 fuentes y 3 conversaciones no. Siguiente paso: `hoja_clave@v2`, medido antes y después.
4. **Citas que existen pero no aplican.** Ver la lectura técnica previa.

### Lectura técnica previa (no vinculante)

> **No mostrar al abogado revisor antes de que califique**: el paquete de revisión no la incluye para no sesgarlo.
> La escribió el equipo técnico, no un abogado.

Varias respuestas citan artículos reales que no aplican al caso. Todas pasaron el validador, que prueba que la cita
existe y que el texto citado está en la fuente, no que la norma sea la aplicable:

| Paso | Lo que se ve | Lo que se esperaría |
|---|---|---|
| C1 | Usa el art. 51 (causas por las que **el trabajador** rescinde) como requisito para que el patrón rescinda | Art. 47-II y los párrafos finales del 47 |
| C2 | Da el plazo de dos años del art. 519 (ejecución de sentencias) y no menciona los dos meses | Art. 518: dos meses desde el día siguiente a la separación |
| C3 | Aplica el art. 283 Ter, que es para trabajadoras del campo | Arts. 133-XV y 170 |
| I1 | Funda la indemnización de tres meses en el art. 436 (terminación colectiva) | Arts. 48 y 50; CPEUM 123-A-XXII |
| A1 | Cita el art. 341, que es para el trabajo del hogar | Arts. 47, 133-XV y 784 |

También: en C1 y en las rondas de Argumentar, el aviso de rescisión se cita a la «fracc. XV». Los párrafos finales
del art. 47 quedaron dentro del bloque de esa fracción al cargar la ley; es un defecto del chunker ya anotado en
el PLAN.

Es la limitación de §12 del blueprint («cita válida no es paráfrasis fiel»), ahora con casos concretos. Si la
revisión del abogado la confirma, cada caso se vuelve un caso golden que la mide.

## Revisión por abogado

**Estado: pendiente.** Nadie ha calificado todavía.

- **Página de revisión:** https://claude.ai/artifact/R2kVEQhyDDRvBmhk1jupFR (privada; se comparte desde su menú).
  Cada respuesta aparece junto al texto completo de cada unidad citada y de su artículo. El abogado califica:
  - por fuente: ¿dice lo que la respuesta le atribuye? (sí / en parte / no / no sé);
  - por respuesta: exactitud jurídica, fidelidad, completitud y utilidad (1–5), y si contiene un error que podría
    perjudicar al cliente;
  - por punto esperado: si el sistema lo cubrió y si el punto es correcto;
  - el verificador, por referencia;
  - una valoración general.

  Las calificaciones se guardan solas, una revisión por persona, en la colección `reviews` del artefacto.
- **Versión imprimible:** `evals/e2e/runs/E2E-LAB-01-20261117T225129Z-revision.md`, con las mismas casillas.
- **Acceso:** si el abogado es del despacho, compartir como Colaborador; si es externo, invitarlo por correo como
  Editor, sin enlace público. Con acceso de solo lectura la página guarda en su navegador y ofrece copiar las
  calificaciones para enviarlas por correo.

**Qué se hace con la revisión:**

1. Los puntos esperados aprobados o corregidos pasan a `evals/golden/` con `reviewed_by: <abogado>`. Serán los
   primeros casos golden revisados por un abogado (hoy son 0).
2. Cada «no» en fidelidad se registra con su cita y entra a la medición de fidelidad.
3. Los resultados se resumen aquí y en `docs/PLAN.md`, con la fecha y el id de la corrida.

## Límites de esta evaluación

- **Es un caso y una corrida.** El modelo local varía entre corridas (PLAN 17-11-2026: la misma hoja salió
  distinta en corridas seguidas), así que nada de esto es una tasa.
- **El caso, las preguntas y los esperados los escribió el equipo técnico con IA.** Pueden tener errores; ya
  encontramos uno (hallazgo 2).
- **Solo materia laboral federal y solo el modelo local** (Gemma 4 26B). El perfil Claude no se corrió.
- **Las respuestas se calificarán en contexto, no contra una sola respuesta correcta:** dos respuestas distintas
  pueden ser igual de buenas.
