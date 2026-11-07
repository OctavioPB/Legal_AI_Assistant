# Brand.md — Fuente de verdad de UI/Frontend (v1 · APROBADA 2026-11-07)

Regla: TODO desarrollo de UI/Frontend obedece este archivo (PLAN.md WS6 y WS0.T05). Si un caso no
está cubierto, se extiende este archivo primero; no se improvisa en el componente. Los PRs de UI
declaran conformidad con la sección que aplican.

**Estado:** v1 aprobada por la persona dueña del producto el 2026-11-07 con las decisiones D1–D4 de
§0 (todas las propuestas por defecto). Cualquier cambio posterior de identidad se hace aquí primero.

## 0. Decisiones de identidad (cerradas 2026-11-07)

| # | Decisión | Resolución |
|---|---|---|
| D1 | Nombre del producto y logotipo | Nombre de trabajo: "Legal AI Assistant", solo wordmark tipográfico (sin símbolo) |
| D2 | Color primario de marca | Azul tinta `#1F3A5F` |
| D3 | Acento | Dorado `#B58A2B` (decorativo; texto pequeño usa `accent-ink`) |
| D4 | Tuteo/usted en es-MX | Formal impersonal ("Seleccione la jurisdicción") |

Cambiar D1–D3 solo toca los tokens de §1 y el wordmark; ningún componente codifica un color.

## 1. Identidad y color

Los colores viven como **tokens CSS arriba** de la hoja de estilos; los overrides de marca van **al
final** (una regla previa con el mismo selector pierde). Nunca un hex en un componente.
Modo claro y oscuro; `prefers-color-scheme` por defecto, con selector manual persistido.

| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `--bg` | `#FAFAF8` | `#0F1318` | fondo de página |
| `--surface` | `#FFFFFF` | `#171C23` | tarjetas, paneles |
| `--text` | `#1B1F24` | `#E8EAED` | texto principal |
| `--text-muted` | `#4A5260` | `#A8B0BC` | texto secundario |
| `--primary` | `#1F3A5F` | `#8DB4E8` | acciones, enlaces |
| `--on-primary` | `#FFFFFF` | `#0F1318` | texto sobre `--primary` relleno |
| `--accent` | `#B58A2B` | `#B58A2B` | detalles decorativos, NUNCA texto pequeño |
| `--accent-ink` | `#7A5A12` | `#E0B85A` | texto pequeño de acento |
| `--border` | `#8A8F98` | `#7B8494` | bordes de controles (≥ 3:1) |
| `--focus` | `#1D5FD6` | `#7FB0FF` | anillo de foco |
| `--verified-fg/bg` | `#0B5D46` / `#E6F3EE` | `#7FD1B1` / `#12332A` | cita verificada |
| `--reasoning-fg/bg` | `#6B4A00` / `#FFF3D6` | `#F2CF7A` / `#3A2D0B` | razonamiento del modelo |
| `--unsupported-fg/bg` | `#2D3A4F` / `#EEF1F6` | `#C9D3E5` / `#1E2735` | contenido sin respaldo (refusal) |

Contraste medido (WCAG 2.1, texto normal ≥ 4.5:1, controles ≥ 3:1): todos los pares de la tabla
cumplen. Claro: texto/fondo 15.85, muted/surface 7.87, primary/fondo 10.99, accent-ink/fondo 6.09,
verified 6.90, reasoning 7.31, unsupported 10.14, borde 3.11, foco 5.49. Oscuro: texto/fondo 15.46,
muted/surface 7.83, primary 8.72, accent-ink 9.92, verified 7.61, reasoning 8.96, unsupported 9.97,
borde 4.94, foco 8.48. El acento puro sobre fondo claro mide 3.03:1 y por eso NO se usa para texto.
Todo token nuevo se agrega con su medición; un test de contraste lee esta tabla (WS6.T08).

## 2. Tipografía (autoalojada)

| Rol | Familia | Licencia |
|---|---|---|
| UI | Inter (variable) | SIL OFL 1.1 |
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
