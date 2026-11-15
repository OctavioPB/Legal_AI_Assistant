# Brand.md — Fuente de verdad de UI/Frontend (v2 · estilo OPB · 2026-11-08)

Regla: TODO desarrollo de UI/Frontend obedece este archivo (PLAN.md WS6 y WS0.T05). Si un caso no
está cubierto, se extiende este archivo primero; no se improvisa en el componente. Los PRs de UI
declaran conformidad con la sección que aplican.

**Estado:** v2 pedida por la persona dueña del producto el 2026-11-08: identidad visual **OPB** (la de
Octavio Pérez Bravo), con la estructura y los componentes de Library-RAG como referencia. Sustituye D2, D3 y
la tipografía de UI de v1 (D5–D7). Siguen intactas las tres marcas (§3), la accesibilidad (§6) y las reglas
técnicas (§7). Cualquier cambio posterior de identidad se hace aquí primero.

## 0. Decisiones de identidad (cerradas 2026-11-07)

| # | Decisión | Resolución |
|---|---|---|
| D1 | Nombre del producto y logotipo | Nombre de trabajo: "Legal AI Assistant", solo wordmark tipográfico (sin símbolo) |
| D2 | Color primario de marca | Azul tinta `#1F3A5F` |
| D3 | Acento | Dorado `#B58A2B` (decorativo; texto pequeño usa `accent-ink`) |
| D4 | Tuteo/usted en es-MX | Formal impersonal ("Seleccione la jurisdicción") |
| D5 (v2) | Primario | Azul OPB `#003366` (sustituye D2) |
| D6 (v2) | Acento | Oro OPB `#C8982A`, decorativo (botón de envío, reglas, marcas); texto pequeño en `accent-ink` `#8A6512` (sustituye D3) |
| D7 (v2) | Tipografía | Títulos Fraunces 300 (cursiva oro en la palabra clave), UI Plus Jakarta Sans; normas siguen en Source Serif 4 e identificadores en JetBrains Mono |
| D8 (v2) | Wordmark | "Legal *AI* Assistant" en Fraunces sobre la barra azul, "AI" en cursiva oro claro |

Cambiar D1–D3 solo toca los tokens de §1 y el wordmark; ningún componente codifica un color.

## 1. Identidad y color

Los colores viven como **tokens CSS arriba** de la hoja de estilos; los overrides de marca van **al
final** (una regla previa con el mismo selector pierde). Nunca un hex en un componente.
Modo claro y oscuro; `prefers-color-scheme` por defecto, con selector manual persistido.

| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `--bg` | `#F4F6F9` | `#061A2F` | fondo de página |
| `--surface` | `#FFFFFF` | `#0B2742` | tarjetas, paneles |
| `--surface-2` | `#EEF2F7` | `#10304F` | rieles (historial, fuentes), controles segmentados |
| `--line` | `#E0EAF4` | `#173B5E` | líneas decorativas de tarjetas y separadores (no de controles) |
| `--text` | `#1C1C2E` | `#EAF0F7` | texto principal |
| `--text-muted` | `#5B6474` | `#A9C1DA` | texto secundario |
| `--primary` | `#003366` | `#8DB8E6` | acciones, enlaces, títulos |
| `--on-primary` | `#FFFFFF` | `#061A2F` | texto sobre `--primary` relleno |
| `--primary-soft` | `#E0EAF4` | `#143A62` | fondos suaves: burbuja del usuario, elemento activo, ámbito |
| `--on-primary-soft` | `#003366` | `#D3E3F4` | texto sobre `--primary-soft` |
| `--accent` | `#C8982A` | `#C8982A` | oro decorativo y botón de envío, NUNCA texto pequeño |
| `--accent-light` | `#E8C46A` | `#E8C46A` | hover del oro, cursiva sobre azul |
| `--accent-ink` | `#8A6512` | `#E8C46A` | texto pequeño en oro (etiquetas, cursivas de títulos) |
| `--on-accent` | `#1C1C2E` | `#1C1C2E` | texto sobre oro relleno |
| `--border` | `#7A8BA0` | `#5C7FA3` | bordes de controles (≥ 3:1) |
| `--focus` | `#336699` | `#99BBDD` | anillo de foco |
| `--nav-bg` | `#003366` | `#021E3A` | barra superior y pie |
| `--verified-fg/bg` | `#155C3A` / `#E0F7EF` | `#8EE3BD` / `#0F3A2A` | cita verificada (verde OPB) |
| `--reasoning-fg/bg` | `#5B2E9E` / `#F1EAFA` | `#D3BFF5` / `#2A1F45` | razonamiento del modelo (morado OPB: análisis estratégico) |
| `--unsupported-fg/bg` | `#2D3A4F` / `#EAEFF5` | `#C9D3E5` / `#12304D` | contenido sin respaldo (refusal) |

Contraste medido (WCAG 2.1, texto normal ≥ 4.5:1, controles ≥ 3:1), 2026-11-08. Claro: texto/fondo 15.46,
texto/surface 16.73, muted/surface 5.97, primary/fondo 11.65, accent-ink/fondo 4.91, on-primary/primary 12.61,
verified 7.13, reasoning 7.68, unsupported 9.93, borde/fondo 3.22, foco 5.54. Oscuro: texto/fondo 15.30,
muted/surface 8.19, primary/fondo 8.47, accent-ink/fondo 10.48, verified 8.36, reasoning 9.08, unsupported 8.96,
borde/fondo 4.20, foco 8.78. Sobre la barra azul: blanco 12.61, oro claro 7.53. El oro puro sobre fondo claro
mide 2.43:1 y por eso NO se usa para texto; sobre él va `--on-accent` (6.36). Todo token nuevo se agrega con su medición; un test de contraste lee esta tabla (WS6.T08).

## 2. Tipografía (autoalojada)

| Rol | Familia | Licencia |
|---|---|---|
| Títulos (h1, h2, wordmark, títulos de tarjeta) | Fraunces 300/400, normal y cursiva | SIL OFL 1.1 |
| UI | Plus Jakarta Sans 400–700 | SIL OFL 1.1 |
| Texto jurídico citado | Source Serif 4 (variable) | SIL OFL 1.1 |
| Identificadores (`cite_id`) | JetBrains Mono | SIL OFL 1.1 |

- Archivos `woff2` dentro del repo en `web/public/fonts/`, con `VERSION.txt` (familia, versión,
  licencia, URL de origen y fecha). **Prohibido** Google Fonts, CDN, trackers o cualquier recurso
  externo: el contexto es privilegiado y un `<link>` externo filtra el referrer. Un test falla si
  un asset referencia una URL externa.
- Escala (rem, base 16 px): 0.8125 · 0.875 · 1 · 1.125 · 1.25 · 1.5 · 1.875 · 2.25. Interlineado 1.5
  en UI, 1.6 en texto jurídico. Longitud de línea del texto jurídico ≤ 75 caracteres.
- Texto de normas y de jurisprudencia: **siempre en serif**, nunca en cursiva por defecto, con
  `lang="es"` o `lang="en"` según el idioma ORIGINAL de la fuente (no el de la interfaz). Una
  traducción se muestra en UI sans con la etiqueta "traducción no oficial".

## 3. Las tres marcas (visualmente distintas y estables)

Ninguna se distingue solo por color: cada una difiere en forma, ícono y rótulo.

| Marca | Significa | Forma | Ícono | Rótulo |
|---|---|---|---|---|
| **Cita verificada** `[cite:]` | el sistema comprobó que existe en las fuentes de esta consulta | chip de borde sólido, esquinas rectas (radio 4 px), tipografía mono | ✓ escudo | `citation_string` |
| **Razonamiento del modelo** `[razonamiento estratégico]` y `[análisis propio]` | lo añade el modelo, sin respaldo normativo | bloque sombreado `--reasoning-bg`, borde izquierdo discontinuo 3 px, cursiva | ◇ rombo | "Razonamiento estratégico" / "Análisis del asistente" + "sin respaldo normativo" |
| **Sin respaldo** `<insuficiente>` | el sistema no puede responder con las fuentes disponibles | tarjeta completa, borde punteado `--unsupported`, fondo rayado sutil | ⌕ lupa | "Fuentes insuficientes" |

Reglas: lo que añade el modelo sin respaldo **nunca** tiene apariencia de cita (sin chip, sin mono,
sin ✓). Una cita que el validador rechazó nunca llega a la UI. El texto del modelo y de las
fuentes se renderiza **como texto** (nunca `dangerouslySetInnerHTML`, nunca HTML crudo).

### 3.1 Chip de cita
- Contenido: `citation_string` (p. ej. "Ley Federal del Trabajo, art. 47, fracc. X"); el `cite_id`
  completo va en `title`/tooltip y en copiar-al-portapapeles, en mono.
- Acción: clic o Enter abre el visor de la fuente en el párrafo exacto (deep-link, texto resaltado
  con `--accent` al 25 % de opacidad). Teclado: foco con anillo `--focus` 2 px + offset 2 px.
- Estados: normal · hover (fondo `--verified-bg` más intenso) · foco · activo · **no vigente**
  (insignia "No vigente" en `--accent-ink` con ícono de reloj; la advertencia de vigencia es
  obligatoria, jurisdictions.md) · cargando fuente (esqueleto) · fuente no disponible (texto plano
  del `citation_string`, sin enlace, con aviso).
- Varias citas consecutivas se agrupan sin separador visual ambiguo (espacio 4 px).

### 3.2 Componente `Refusal` (primera clase, no es un error)
Estructura fija: ícono + título "Fuentes insuficientes" · **Qué falta** (norma/precedente que habría
que localizar, texto de catálogo según `reason_key`) · **Qué puede hacer** (acciones sugeridas:
ampliar la jurisdicción, subir el documento faltante, reformular). Nunca usa rojo ni el patrón de
alerta de error; nunca se presenta como fallo del sistema. Se anuncia a lectores de pantalla con
`role="status"`. Cada `reason_key` del servidor (`retrieval.*`, `validation.*`) tiene entrada en el
catálogo en ambos idiomas; el texto lo escribe el catálogo, jamás el modelo.

### 3.3 Modo adversarial
`[razonamiento estratégico]` se muestra con la marca de razonamiento (§3); el panel "Vulnerabilidades
priorizadas" es una lista numerada 3–5 con la fuente faltante que resolvería cada punto, y
conserva ambos tipos de marca visibles y distinguibles.

### 3.4 Laguna de sección y etiqueta de párrafo
- **Laguna de sección** (`<insuficiente></insuficiente>` dentro de una respuesta fundamentada, p. ej. una
  sección de jurisprudencia sin fuentes recuperadas): forma pequeña de la marca 3, párrafo con borde
  punteado `--unsupported`, ícono ⌕ y el texto del catálogo "Sin fuentes suficientes para esta sección".
  Lo que el modelo haya escrito dentro del bloque se descarta; nunca se muestra.
- **Etiqueta de párrafo:** `[razonamiento estratégico]` / `[análisis propio]` solas en su línea marcan el
  párrafo siguiente, hasta una línea en blanco. Al inicio de un renglón (antes de su primer punto) marcan ese renglón completo (ADR-021). Se muestran con la marca 2 (sombreado, borde discontinuo,
  cursiva, rótulo "sin respaldo normativo").
- **Chips repetidos:** cada afirmación lleva su propia cita (regla del validador), así que una respuesta
  puede tener decenas de chips de la misma fuente. Pendiente de diseño: variante compacta para repeticiones
  consecutivas del mismo `cite_id` (no se improvisa en el componente).

### 3.5 Visor de fuentes
Diálogo modal nativo (`<dialog>`: foco atrapado, Esc cierra). Muestra la cita completa, la jerarquía
(breadcrumb), documento, versión e insignia de vigencia ("Vigente" / "No vigente"), el identificador en mono
y el artículo completo con el pasaje citado resaltado (`--accent` al 25 %). El artículo es una región con
scroll alcanzable por teclado (`tabindex=0`, `role=region`). El idioma del texto (`lang`) es el de la fuente.

## 4. Componentes base
- **Selector de jurisdicción:** país → estado → municipio en cascada, valores solo del catálogo
  cerrado. Las opciones inexistentes no se pueden escribir (select/combobox sin texto libre); un nivel
  superior sin selección deshabilita el inferior. Muestra siempre el alcance efectivo apilado
  ("México · Federal + Nuevo León + Monterrey"). Cambiarlo es acción del usuario, nunca inferencia.
- **Selector de persona** (general, laboral, penal, corporativo, corporativo-financiero) y de
  **idioma** (es-MX / en-US, sin recarga).
- **Estados:** vacío (qué hacer primero, sin ilustraciones decorativas), cargando (esqueleto, nunca
  spinner infinito), transmitiendo (cursor discreto; respeta `prefers-reduced-motion`), error de
  red/servidor (lenguaje distinto al refusal, con reintento).
- **Dossier:** secciones I–V con jerarquía tipográfica fija; la exportación a PDF conserva citas
  (como `citation_string` + número de nota con lista de fuentes) y la jerarquía.

## 5. Tono y microcopy
- Formal profesional, claro y sin adornos; sin promesas de asesoría legal. Pie permanente:
  "Herramienta de investigación. No sustituye el criterio profesional del abogado." / "Research
  tool. It does not replace a lawyer's professional judgment."
- Verbos de acción concretos ("Abrir fuente", "Ampliar jurisdicción"); sin exclamaciones, sin
  humor, sin antropomorfismo ("creo que", "me parece").
- **El microcopy es un catálogo** (next-intl, `es-MX` y `en-US`), claves `área.componente.clave`
  (p. ej. `chat.refusal.title`). Paridad de claves verificada por test; ningún literal visible
  en componentes. Los mensajes del servidor (refusals, vigencia) llegan como **claves**, no texto.

## 6. Accesibilidad (WCAG 2.1 AA)
- Contraste según §1; foco siempre visible; todo operable con teclado en orden lógico; áreas
  táctiles ≥ 44 px; tamaño de texto escalable al 200 % sin pérdida.
- Las tres marcas son comprensibles sin color (forma + ícono + rótulo). Íconos decorativos
  `aria-hidden`; el significado va en texto accesible ("Cita verificada: …").
- Contenido transmitido: región `aria-live="polite"` que no re-lee el texto ya anunciado.
- `lang` correcto por fragmento (fuente original vs. interfaz). `prefers-reduced-motion` y
  `prefers-contrast` respetados.

## 7. Reglas técnicas (verificadas por test, WS6.T08)
1. Sin recursos externos (fuentes, scripts, imágenes, estilos); CSP estricta `default-src 'self'`.
2. Tokens arriba, overrides de marca al final.
3. Salida del modelo y texto jurídico solo como texto.
4. Paridad de catálogos es-MX/en-US; sin literales de texto de usuario fuera del catálogo.
5. Contraste de los tokens de §1 ≥ AA, leído desde esta tabla.

## 8. Espacios de trabajo: Conversar · Investigación · Argumentar (ADR-016, 2026-11-08)

Estructura tomada de Library-RAG; identidad (color, tipografía, tono) de este archivo, sin cambios en §0–§2.

### 8.0 Uso sin configuración (personas no técnicas, 2026-11-08)
- **Arranque:** doble clic en `Iniciar Legal AI.cmd` (cierre: `Detener Legal AI.cmd`); nada se teclea en una terminal.
- **Inicio (`/`):** un `h1` en pregunta ("¿Qué necesita hacer hoy?"), tres tarjetas enlace (título `--primary`,
  qué hace, "Úselo para…", llamada a la acción) y "Cómo funciona" en 4 pasos, incluido qué significa un rechazo.
- **Login:** recuerda despacho y correo en ese navegador (nunca la contraseña), "Mostrar contraseña", el cursor
  empieza en el primer campo vacío y dice a quién pedir el acceso.
- **Valores por omisión que funcionan:** el ámbito se muestra en UNA línea ("Se consultará: Federal (México) ·
  General · solo normas vigentes") con un botón "Cambiar" (`aria-expanded`); los selectores aparecen solo al
  pedirlos y cada opción explica su efecto en lenguaje llano.
- **Ejemplos:** cada espacio ofrece preguntas de ejemplo (botones de borde discontinuo); un clic llena el composer y
  le da el foco, nunca envía. Argumentar ofrece "Cargar un caso de ejemplo".
- **Teclado y lector de pantalla:** todo control visible se alcanza con Tab (verificado por prueba en cada página);
  abrir una conversación mueve el foco a su título; al terminar un turno se anuncia una sola vez "La respuesta está
  lista" / "No hay fuentes suficientes…"; el composer dice "Ctrl + Enter para enviar".
- **Una respuesta a la vez:** si hay un turno en curso en otra conversación se dice ("Podrá enviar cuando
  termine"); la respuesta se guarda en SU conversación aunque el usuario haya cambiado de pantalla.
- **Teléfono (≤ 40 rem):** idioma, tema y "Cerrar sesión" van tras el botón "Ajustes"; sin desplazamiento lateral.

### 8.1 Marco de página
- **Barra superior:** wordmark · navegación de las tres páginas · idioma · tema · "Cerrar sesión". La página
  activa se marca con `aria-current="page"`, peso 650, color `--primary` y subrayado de 3 px en `--accent`
  (decorativo; el acento nunca colorea texto, §1). En `/login` no hay navegación.
- **Cuerpo en tres columnas** (≥ 64 rem): historial (17 rem, `--surface`) | contenido (máx. 56 rem) |
  fuentes (19 rem, `--surface`); Investigación no tiene columna de fuentes (cada expediente lista las suyas).
  Bajo 64 rem: una columna (historial, contenido, fuentes), el historial empieza **cerrado** y el composer
  deja de ser fijo (taparía el caso y las respuestas).
- **Encabezado de página:** un único `h1` con el nombre de la página y el botón "Ocultar/Mostrar historial"
  (`aria-expanded`, `aria-controls`). La región principal NO es `aria-live` (re-leería respuestas enteras):
  la etapa en curso se anuncia con `role="status"`.

### 8.2 Historial
- Botón primario "Nueva …" arriba, búsqueda (título y contenido de los mensajes), lista más reciente primero.
- Cada elemento: título (600) y metadatos (fecha y hora **locales** del usuario · número de mensajes, 0.8125 rem,
  `--text-muted`). El abierto: borde izquierdo 3 px `--accent` y fondo `--bg`, con `aria-current="page"`.
- Vacío: texto de catálogo, sin ilustración. Solo lista conversaciones del propio usuario.

### 8.3 Conversación
- **Nueva:** tarjeta de bienvenida (`h2`, explicación, selector de jurisdicción y perfil). **Existente:** barra con
  título (`h2`), "Renombrar" y "Eliminar" (confirmación en línea, sin diálogo nativo) y el **ámbito fijado** como
  chips de solo lectura ("Ámbito fijado al iniciar; para cambiarlo, inicie una nueva": cambiarlo es acción del
  usuario, nunca inferencia, §4).
- **Turno del usuario:** bloque `--bg` con borde izquierdo 3 px `--primary`, rótulo ("Su consulta" / "Su argumento")
  y texto tal como se escribió (`white-space: pre-wrap`, como texto).
- **Respuesta:** `AnswerView` compacto (sin título visible ni lista propia de fuentes: las muestra el panel) o la
  tarjeta `Refusal` de §3.2. Las marcas de §3 no cambian.
- **En curso:** borde discontinuo, etapa (`role="status"`), tiempo transcurrido (no anunciado) y la nota de
  duración del modelo local. Nunca un spinner infinito (§4).
- **Composer:** etiqueta visible, contador de caracteres, Ctrl/⌘+Enter envía; si el turno falla, el texto se
  conserva. Fijo al pie en pantallas anchas.

### 8.4 Panel de fuentes
Lista numerada de las fuentes citadas en toda la conversación (cada `cite_id` una vez, la respuesta más reciente
primero), con el chip de §3.1; abre el visor de §3.5.

### 8.5 Investigación (expediente)
Cada pregunta es un expediente (`article`): rótulo "Pregunta", la pregunta (`h3`) y la respuesta completa con su
lista numerada de fuentes. "Exportar PDF" usa la impresión del navegador: se ocultan barra, historial, composer y
botones; cada cita conserva su `citation_string` más un **número de nota** que corresponde a la lista de fuentes
(§4, Dossier); un expediente por página.

### 8.6 Argumentar (caso y rondas)
- **Ficha del caso:** `details` abierto por defecto, borde izquierdo 3 px `--accent`; resumen "Caso · {parte}";
  hechos y pretensión como texto. La parte sale de un catálogo cerrado (actora, demandada, defensa, acusación,
  tercero interesado). Aviso de privacidad bajo el formulario.
- **Ronda:** el argumento del usuario (8.3) y la respuesta con rótulo "Ataque de la contraparte" o "Argumento
  fortalecido". El cierre "Vulnerabilidades priorizadas" / "Puntos a reforzar" sale del cuerpo y se muestra como
  **panel** (borde superior 3 px `--primary`, fondo `--bg`, lista **numerada**, §3.3), con ambas marcas visibles.
- **Composer:** "Enviar a la contraparte" (primario) y "Fortalecer mi argumento" (secundario).

## 9. Herramientas: Leyes · Verificar · Asuntos · Reformas (ADR-017/018/019, 2026-11-08)

Mismo marco que §8 (barra, tokens, tipografía, tono); ninguna herramienta usa el modelo para el contenido jurídico.

### 9.0 Navegación e inicio
- La barra suma "Leyes", "Verificar" y "Asuntos" tras los tres espacios (mismo estilo de §8.1). "Reformas" se abre
  desde el inicio y desde el aviso.
- Inicio: debajo de las tres tarjetas, `h2` "Herramientas" con cuatro tarjetas del mismo componente. Si algo del
  trabajo del usuario cita una disposición reformada, aparece **antes** de las tarjetas un aviso (borde
  `--accent-ink`, borde izquierdo 3 px `--accent`, ícono ◷, `h2` "Hay reformas que afectan su trabajo", número de
  citas y botón "Revisar"). Sin alertas, no hay aviso (nunca un aviso vacío).

### 9.1 Leyes (navegador)
- Dos columnas (≥ 64 rem): estructura (22 rem, `--surface`, con scroll propio) | artículo. Una columna debajo.
- Estructura: selector de ley (catálogo de leyes cargadas, con su jurisdicción), versión y última reforma
  registrada, "Ir al artículo" (número libre: "47", "50 Bis"; si no existe: "Esa ley no tiene ese artículo",
  `role=status`), y el árbol como `details`/`summary` anidados; los artículos son botones con rótulo (600) y su
  inicio en `--text-muted`. El abierto: borde izquierdo 3 px `--accent`, fondo `--bg`.
- Artículo: ruta (título › capítulo), `h2` con el rótulo (recibe el foco al abrir), texto **serif** en una caja
  `--surface` con `lang` de la fuente, "← anterior / siguiente →" y "Partes citables": cada parte con su
  `citation_string`, el `cite_id` en mono, insignias "No vigente" o "◷ Reformada · fecha" (§3.1), "Copiar cita" y
  "Guardar en asunto".
- "Versiones": comparar la vigente con una anterior. Lo agregado va en `<ins>` (subrayado 2 px sobre
  `--verified-bg`), lo eliminado en `<del>` (tachado sobre `--unsupported-bg`): forma + color, nunca solo color;
  leyenda en texto. La URL guarda ley y artículo (`?ley=&p=`).

### 9.2 Verificar (citas de un escrito)
- Formulario en tarjeta: grupo de opciones "Pegar el texto" / "Subir un PDF" (radio: una parada de Tab, flechas
  dentro), nombre del escrito, texto o archivo, asunto opcional y el aviso fijo **"El escrito no se guarda"**.
  "Cargar un texto de ejemplo" llena el campo y no envía.
- Resultado (`h2` que recibe el foco): resumen por estado y una lista **numerada** de hallazgos. Cada estado tiene
  ícono + rótulo + forma, nunca solo color: Vigente ✓ (estilo verificado), Derogado / No vigente ◷ (`--accent-ink`),
  No existe ⌕ (punteado `--unsupported`), Ley no cargada ○ (texto `--text-muted`), Ley no identificada / ambigua ◇
  (discontinuo `--reasoning`, "revise"). La cita textual: "coincide" / "NO coincide" / "coincide con una versión
  anterior" / "demasiado corta". Una ley tomada del contexto se dice ("Confírmelo").
- Con texto pegado (o PDF recién leído) cada hallazgo muestra su fragmento del escrito con la referencia en `<mark>`;
  un reporte guardado no tiene texto (ADR-019) y no lo muestra. Las fuentes abren el visor de §3.5.
- Pie: "La verificación comprueba existencia, vigencia y coincidencia textual. No juzga la interpretación."

### 9.3 Asuntos
- Lista: formulario "Nuevo asunto" (nombre, referencia del cliente, descripción, aviso de privacidad) y tarjetas
  (borde superior 3 px `--primary`; título `--primary` 650; referencia · papel · elementos · personas).
- Asunto abierto (`h2` con foco, borde izquierdo 3 px `--accent`): acciones de la persona responsable (cerrar /
  reabrir, eliminar con confirmación en línea que dice que las conversaciones NO se borran), y secciones `h3`:
  Trabajo (enlaces "Nueva en Conversar/Investigación/Argumentar" y "Verificar un escrito", que abren la página con
  el asunto preseleccionado; lista de conversaciones con su página, fecha y "de un colega"), Fuentes guardadas
  (chip §3.1 + insignia de reforma + "Abrir la versión vigente"), Notas, Personas con acceso (agregar colega del
  despacho de una lista cerrada; solo la persona responsable administra).
- En los tres espacios: selector "Asunto" opcional al crear; en la barra de una conversación propia, selector
  compacto para moverla; una conversación de un colega se muestra sin "Renombrar/Eliminar" y con la nota
  "compartida por el asunto". "Guardar en asunto" vive en el visor de fuentes y en Leyes.

### 9.4 Reformas
- `h2` "Su trabajo afectado": cada alerta con insignia ◷ (cambio · fecha), la cita, dónde está (conversación,
  fuente guardada, verificación; enlace) y "Abrir lo citado" / "Abrir la versión vigente".
- "Reformas registradas": ley, versiones, fecha de publicación, conteos por tipo de cambio y "Ver qué cambió".
- "Última revisión de cada fuente": tabla (ley, revisada, resultado en palabras). Si una fuente rechaza la
  descarga automática se dice "La fuente rechazó la descarga" (no se insiste, ADR-017).
- El visor de fuentes (§3.5) muestra, si aplica, "Esta disposición cambió después de citarse (…)" en
  `--accent-ink` con la versión vigente en mono.

### 9.6 Modelo de una conversación (ADR-024, 2026-11-09)
- Al crear (Conversar, Investigación, Argumentar): `fieldset` "Modelo" con una tarjeta-radio por modelo USABLE
  (configurado en el servidor y permitido por el despacho); se oculta si solo hay uno. Cada tarjeta: nombre,
  insignia de ubicación ("En este equipo" verde §3; "Externo · Anthropic" en `--accent-ink`), "predeterminado"
  si aplica, una línea de para qué sirve y la línea de medición: "Medido (fecha, N casos): respondió X de las
  preguntas con fuente · rechazó Y de las que no la tienen · citas válidas · segundos típicos (casos aún sin
  revisión de abogado)" o "Sin medir". No se muestra recall de recuperación (es igual para todos los modelos).
  Nunca "recomendado" ni estrellas: la medición habla. Tarjeta elegida: borde `--primary`, fondo `--primary-soft`.
- Un modelo externo muestra, ANTES de enviar nada, un aviso con borde izquierdo 3 px `--accent`: qué sale del
  equipo (pregunta, historial, fragmentos; en Argumentar también hechos y argumentos) y a quién. La nota de
  privacidad del caso cambia a su versión "externo"; nunca se dice "no sale de este equipo" si sale.
- Conversación abierta: chip "Modelo: …" junto al ámbito (fijo, como jurisdicción y perfil); el externo, en
  `--accent-ink` con "· externo".
- Ajustes › "Modelos externos": casilla "Permitir que el despacho use Claude (Anthropic)" con su explicación;
  solo la cambia un administrador (los demás la ven deshabilitada con "Solo un administrador…"); si el servidor
  no tiene clave, se dice que Claude no está disponible aunque se permita.
- Errores en palabras: "El despacho no permite modelos externos…", "Ese modelo no está configurado…", "El
  proveedor del modelo externo rechazó la solicitud…".

## 10. Estilo OPB (v2, 2026-11-08): cómo se aplica

Referencia de estructura: Library-RAG (misma familia de producto). Todo vive en la sección 4 de `globals.css`.
- **Barra superior:** banda `--nav-bg` con una línea superior de 3 px en degradado azul → oro; wordmark en
  Fraunces blanco con "AI" en cursiva `--accent-light`; las páginas son pastillas (activa: fondo blanco al 14 % y
  subrayado oro de 2 px); **idioma, tema y "Cerrar sesión" van en el panel "Ajustes"** (diálogo lateral nativo,
  borde superior oro), a todo ancho de pantalla.
- **Títulos:** h1/h2 en Fraunces 300 color `--primary`; la palabra clave en cursiva `--accent-ink`. Etiquetas
  ("eyebrow"): 11 px, mayúsculas, tracking amplio, `--accent-ink`, precedidas por una regla oro de 24 px.
- **Inicio:** héroe azul con textura de rejilla de 48 px y un resplandor oro, etiqueta, título con cursiva y
  texto `#D3E3F4`; tarjetas blancas con borde superior `--primary` (hover: oro) y título en Fraunces; "Cómo
  funciona" como pasos numerados en círculos azules.
- **Espacios de trabajo:** historial y fuentes en rieles `--surface-2`; elemento activo en `--primary-soft`.
  El turno del usuario es una **burbuja a la derecha** (`--primary-soft`, esquinas 16/4 px); la respuesta se lee
  como documento a la izquierda. El composer es una caja redondeada con sombra suave y el botón principal en
  **oro** con texto `--on-accent`; los ejemplos son pastillas.
- **Tarjetas:** `--surface`, borde `--line`, radio 14 px, sombra suave; las de acción llevan borde superior de
  3 px (`--primary` o `--accent`). Tablas: encabezado azul con etiquetas en mayúsculas y filas alternas.
- **Pie:** franja `--nav-bg`, texto `#B8C7D8`.
- **Respuestas (2026-11-09):** el texto del asistente va en Plus Jakarta Sans (1 rem, interlineado 1.7, ≤ 72 ch);
  la serif queda SOLO para el texto de normas (visor de fuentes, Leyes). Así se distingue a simple vista "lo que
  dice la ley" de "lo que explica el asistente".
- **Citas en línea:** dentro de una respuesta, la cita es una pastilla compacta en sans (0.8125 rem, peso 600, ✓,
  `--verified`), sin romper el renglón (excepción de WCAG 2.5.8 para objetivos en línea); en el panel de fuentes
  conserva el tamaño táctil completo. El `cite_id` en mono sigue en el tooltip y en el visor (§3.1).
- **Fechas:** siempre en el idioma de la interfaz y la zona horaria del usuario ("8 nov 2026, 19:31").
- **Selector de asunto:** solo aparece si el usuario tiene asuntos abiertos; en la barra de la conversación va en
  una fila (etiqueta + selector).
- Las marcas de §3 conservan forma, ícono y rótulo; solo cambian sus colores a la paleta OPB (verde verificada,
  morado razonamiento, pizarra sin respaldo).
