# Brand.md — Source of truth for UI/Frontend (v2 · OPB style · 2026-11-08)

Rule: ALL UI/Frontend development obeys this file (PLAN.md WS6 and WS0.T05). If a case is not
covered, this file is extended first; nothing is improvised in the component. UI PRs
declare conformity with the section they apply.

> Language note: this document is in English; the interface it describes is in Spanish (es-MX) with an
> English (en-US) catalog. Text in «quotes» or "quotes" that names a button, heading or message is the
> label as it appears on screen (Spanish), and is kept as is because tests and screenshots match on it.

**Status:** v2 requested by the product owner on 2026-11-08: the **OPB** visual identity (that of
Octavio Pérez Bravo), with the structure and components of Library-RAG as a reference. It replaces D2, D3 and
the UI typography of v1 (D5–D7). The three marks (§3), accessibility (§6) and the technical rules (§7) remain
intact. Any later change of identity is made here first.

## 0. Identity decisions (closed 2026-11-07)

| # | Decision | Resolution |
|---|---|---|
| D1 | Product name and logo | Working name: "Legal AI Assistant", typographic wordmark only (no symbol) |
| D2 | Primary brand colour | Ink blue `#1F3A5F` |
| D3 | Accent | Gold `#B58A2B` (decorative; small text uses `accent-ink`) |
| D4 | Formal/informal address in es-MX | Formal impersonal ("Seleccione la jurisdicción") |
| D5 (v2) | Primary | OPB blue `#003366` (replaces D2) |
| D6 (v2) | Accent | OPB gold `#C8982A`, decorative (send button, rules, marks); small text in `accent-ink` `#8A6512` (replaces D3) |
| D7 (v2) | Typography | Headings Fraunces 300 (gold italic on the key word), UI Plus Jakarta Sans; statutes remain in Source Serif 4 and identifiers in JetBrains Mono |
| D8 (v2) | Wordmark | "Legal *AI* Assistant" in Fraunces on the blue bar, "AI" in light-gold italic |

Changing D1–D3 only touches the tokens of §1 and the wordmark; no component hardcodes a colour.

## 1. Identity and colour

Colours live as **CSS tokens at the top** of the stylesheet; brand overrides go **at the
end** (an earlier rule with the same selector loses). Never a hex in a component.
Light and dark mode; `prefers-color-scheme` by default, with a persisted manual selector.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F4F6F9` | `#061A2F` | page background |
| `--surface` | `#FFFFFF` | `#0B2742` | cards, panels |
| `--surface-2` | `#EEF2F7` | `#10304F` | rails (history, sources), segmented controls |
| `--line` | `#E0EAF4` | `#173B5E` | decorative lines of cards and separators (not of controls) |
| `--text` | `#1C1C2E` | `#EAF0F7` | main text |
| `--text-muted` | `#5B6474` | `#A9C1DA` | secondary text |
| `--primary` | `#003366` | `#8DB8E6` | actions, links, headings |
| `--on-primary` | `#FFFFFF` | `#061A2F` | text on a filled `--primary` |
| `--primary-soft` | `#E0EAF4` | `#143A62` | soft backgrounds: the user's bubble, active item, scope |
| `--on-primary-soft` | `#003366` | `#D3E3F4` | text on `--primary-soft` |
| `--accent` | `#C8982A` | `#C8982A` | decorative gold and send button, NEVER small text |
| `--accent-light` | `#E8C46A` | `#E8C46A` | gold hover, italic on blue |
| `--accent-ink` | `#8A6512` | `#E8C46A` | small text in gold (labels, heading italics) |
| `--on-accent` | `#1C1C2E` | `#1C1C2E` | text on a filled gold |
| `--border` | `#7A8BA0` | `#5C7FA3` | borders of controls (≥ 3:1) |
| `--focus` | `#336699` | `#99BBDD` | focus ring |
| `--nav-bg` | `#003366` | `#021E3A` | top bar and footer |
| `--verified-fg/bg` | `#155C3A` / `#E0F7EF` | `#8EE3BD` / `#0F3A2A` | verified citation (OPB green) |
| `--reasoning-fg/bg` | `#5B2E9E` / `#F1EAFA` | `#D3BFF5` / `#2A1F45` | model reasoning (OPB purple: strategic analysis) |
| `--unsupported-fg/bg` | `#2D3A4F` / `#EAEFF5` | `#C9D3E5` / `#12304D` | unsupported content (refusal) |

Measured contrast (WCAG 2.1, normal text ≥ 4.5:1, controls ≥ 3:1), 2026-11-08. Light: text/background 15.46,
text/surface 16.73, muted/surface 5.97, primary/background 11.65, accent-ink/background 4.91, on-primary/primary 12.61,
verified 7.13, reasoning 7.68, unsupported 9.93, border/background 3.22, focus 5.54. Dark: text/background 15.30,
muted/surface 8.19, primary/background 8.47, accent-ink/background 10.48, verified 8.36, reasoning 9.08, unsupported 8.96,
border/background 4.20, focus 8.78. On the blue bar: white 12.61, light gold 7.53. Pure gold on a light background
measures 2.43:1 and is therefore NOT used for text; `--on-accent` goes on top of it (6.36). Every new token is added with its measurement; a contrast test reads this table (WS6.T08).

## 2. Typography (self-hosted)

| Role | Family | Licence |
|---|---|---|
| Headings (h1, h2, wordmark, card titles) | Fraunces 300/400, normal and italic | SIL OFL 1.1 |
| UI | Plus Jakarta Sans 400–700 | SIL OFL 1.1 |
| Quoted legal text | Source Serif 4 (variable) | SIL OFL 1.1 |
| Identifiers (`cite_id`) | JetBrains Mono | SIL OFL 1.1 |

- `woff2` files inside the repo at `web/public/fonts/`, with `VERSION.txt` (family, version,
  licence, source URL and date). **Forbidden:** Google Fonts, CDNs, trackers or any external
  resource: the context is privileged and an external `<link>` leaks the referrer. A test fails if
  an asset references an external URL.
- Scale (rem, 16 px base): 0.8125 · 0.875 · 1 · 1.125 · 1.25 · 1.5 · 1.875 · 2.25. Line height 1.5
  in the UI, 1.6 in legal text. Line length of legal text ≤ 75 characters.
- Text of statutes and case law: **always serif**, never italic by default, with
  `lang="es"` or `lang="en"` according to the ORIGINAL language of the source (not the interface's). A
  translation is shown in UI sans with the label "traducción no oficial".

## 3. The three marks (visually distinct and stable)

None is distinguished by colour alone: each differs in shape, icon and label.

| Mark | Means | Shape | Icon | Label |
|---|---|---|---|---|
| **Verified citation** `[cite:]` | the system checked that it exists in the sources of this query | chip with a solid border, square corners (4 px radius), mono type | ✓ shield | `citation_string` |
| **Model reasoning** `[razonamiento estratégico]` and `[análisis propio]` | added by the model, with no normative support | shaded block `--reasoning-bg`, 3 px dashed left border, italic | ◇ diamond | "Razonamiento estratégico" / "Análisis del asistente" + "sin respaldo normativo" |
| **Unsupported** `<insuficiente>` | the system cannot answer with the available sources | full card, dotted `--unsupported` border, subtle striped background | ⌕ magnifier | "Fuentes insuficientes" |

Rules: what the model adds without support **never** looks like a citation (no chip, no mono,
no ✓). A citation the validator rejected never reaches the UI. Text from the model and from the
sources is rendered **as text** (never `dangerouslySetInnerHTML`, never raw HTML).

### 3.1 Citation chip
- Content: `citation_string` (e.g. "Ley Federal del Trabajo, art. 47, fracc. X"); the full `cite_id`
  goes in the `title`/tooltip and in copy-to-clipboard, in mono.
- Action: click or Enter opens the source viewer at the exact paragraph (deep link, text highlighted
  with `--accent` at 25 % opacity). Keyboard: focus with a 2 px `--focus` ring + 2 px offset.
- States: normal · hover (a stronger `--verified-bg` background) · focus · active · **not in force**
  ("No vigente" badge in `--accent-ink` with a clock icon; the validity warning is
  mandatory, jurisdictions.md) · loading source (skeleton) · source unavailable (plain text
  of the `citation_string`, no link, with a notice).
- Several consecutive citations are grouped without an ambiguous visual separator (4 px space).

### 3.2 `Refusal` component (first class, not an error)
Fixed structure: icon + title "Fuentes insuficientes" · **What is missing** (the rule/precedent that would
have to be located, catalog text according to `reason_key`) · **What you can do** (suggested actions:
widen the jurisdiction, upload the missing document, rephrase). It never uses red or the error
alert pattern; it is never presented as a system failure. It is announced to screen readers with
`role="status"`. Each server `reason_key` (`retrieval.*`, `validation.*`) has an entry in the
catalog in both languages; the text is written by the catalog, never by the model.

### 3.3 Adversarial mode
`[razonamiento estratégico]` is shown with the reasoning mark (§3); the "Vulnerabilidades
priorizadas" panel is a numbered list of 3–5 with the missing source that would resolve each point, and
keeps both kinds of mark visible and distinguishable.

### 3.4 Section gap and paragraph label
- **Section gap** (`<insuficiente></insuficiente>` inside a grounded answer, e.g. a
  case-law section with no retrieved sources): a small form of mark 3, a paragraph with a dotted
  `--unsupported` border, the ⌕ icon and the catalog text "Sin fuentes suficientes para esta sección".
  Whatever the model wrote inside the block is discarded; it is never shown.
- **Paragraph label:** `[razonamiento estratégico]` / `[análisis propio]` alone on their line mark the
  next paragraph, up to a blank line. At the start of a line (before its first period) they mark that whole line (ADR-021). They are shown with mark 2 (shading, dashed border,
  italic, label "sin respaldo normativo").
- **Repeated chips:** each claim carries its own citation (validator rule), so an answer
  can have dozens of chips of the same source. Pending design: a compact variant for
  consecutive repetitions of the same `cite_id` (nothing is improvised in the component).

### 3.5 Source viewer
A native modal dialog (`<dialog>`: focus trapped, Esc closes). It shows the full citation, the hierarchy
(breadcrumb), document, version and validity badge ("Vigente" / "No vigente"), the identifier in mono
and the whole article with the cited passage highlighted (`--accent` at 25 %). The article is a region with
scroll reachable by keyboard (`tabindex=0`, `role=region`). The text's language (`lang`) is the source's.

## 4. Base components
- **Jurisdiction selector:** country → state → municipality in a cascade, values only from the closed
  catalog. Nonexistent options cannot be typed (select/combobox with no free text); a
  higher level without a selection disables the lower one. It always shows the effective stacked scope
  ("México · Federal + Nuevo León + Monterrey"). Changing it is a user action, never an inference.
- **Persona selector** (general, labor, criminal, corporate, corporate-financial) and **language**
  (es-MX / en-US, without a reload).
- **States:** empty (what to do first, no decorative illustrations), loading (skeleton, never an
  endless spinner), streaming (a discreet cursor; respects `prefers-reduced-motion`), network/server
  error (language distinct from the refusal, with retry).
- **Dossier:** sections I–V with a fixed typographic hierarchy; PDF export keeps citations
  (as `citation_string` + a note number with a list of sources) and the hierarchy.

## 5. Tone and microcopy
- Formal, professional, clear and unadorned; no promises of legal advice. Permanent footer:
  "Herramienta de investigación. No sustituye el criterio profesional del abogado." / "Research
  tool. It does not replace a lawyer's professional judgment."
- Concrete action verbs ("Abrir fuente", "Ampliar jurisdicción"); no exclamations, no
  humour, no anthropomorphism ("I think", "it seems to me").
- **Microcopy is a catalog** (next-intl, `es-MX` and `en-US`), keys `area.component.key`
  (e.g. `chat.refusal.title`). Key parity is verified by a test; no visible literal
  in components. Server messages (refusals, validity) arrive as **keys**, not text.

## 6. Accessibility (WCAG 2.1 AA)
- Contrast according to §1; focus always visible; everything operable by keyboard in a logical order; touch
  targets ≥ 44 px; text size scalable to 200 % without loss.
- The three marks are understandable without colour (shape + icon + label). Decorative icons are
  `aria-hidden`; meaning goes in accessible text ("Cita verificada: …").
- Streamed content: an `aria-live="polite"` region that does not re-read text already announced.
- Correct `lang` per fragment (original source vs. interface). `prefers-reduced-motion` and
  `prefers-contrast` respected.

## 7. Technical rules (verified by tests, WS6.T08)
1. No external resources (fonts, scripts, images, styles); strict CSP `default-src 'self'`.
2. Tokens at the top, brand overrides at the end.
3. Model output and legal text only as text.
4. es-MX/en-US catalog parity; no user-facing text literals outside the catalog.
5. Contrast of the §1 tokens ≥ AA, read from this table.

## 8. Workspaces: Conversar · Investigación · Argumentar (ADR-016, 2026-11-08)

Structure taken from Library-RAG; identity (colour, typography, tone) from this file, with no changes to §0–§2.

### 8.0 Use without configuration (non-technical people, 2026-11-08)
- **Start-up:** double-click `Iniciar Legal AI.cmd` (to close: `Detener Legal AI.cmd`); nothing is typed in a terminal.
- **Start page (`/`):** an `h1` as a question ("¿Qué necesita hacer hoy?"), three link cards (title `--primary`,
  what it does, "Úselo para…", call to action) and "Cómo funciona" in 4 steps, including what a refusal means.
- **Login:** remembers firm and email in that browser (never the password), "Mostrar contraseña", the cursor
  starts in the first empty field and it says whom to ask for access.
- **Defaults that work:** the scope is shown on ONE line ("Se consultará: Federal (México) ·
  General · solo normas vigentes") with a "Cambiar" button (`aria-expanded`); the selectors appear only when
  requested and each option explains its effect in plain language.
- **Examples:** each workspace offers example questions (dashed-border buttons); a click fills the composer and
  gives it focus, never sends. Argumentar offers "Cargar un caso de ejemplo".
- **Keyboard and screen reader:** every visible control is reachable with Tab (verified by a test on each page);
  opening a conversation moves focus to its title; when a turn finishes, "La respuesta está
  lista" / "No hay fuentes suficientes…" is announced once; the composer says "Ctrl + Enter para enviar".
- **One answer at a time:** if a turn is in progress in another conversation it says so ("Podrá enviar cuando
  termine"); the answer is saved in ITS conversation even if the user has changed screen.
- **Phone (≤ 40 rem):** language, theme and "Cerrar sesión" go behind the "Ajustes" button; no sideways scrolling.

### 8.1 Page frame
- **Top bar:** wordmark · navigation of the three pages · language · theme · "Cerrar sesión". The active
  page is marked with `aria-current="page"`, weight 650, colour `--primary` and a 3 px underline in `--accent`
  (decorative; the accent never colours text, §1). There is no navigation on `/login`.
- **Three-column body** (≥ 64 rem): history (17 rem, `--surface`) | content (max 56 rem) |
  sources (19 rem, `--surface`); Investigación has no sources column (each file lists its own).
  Below 64 rem: one column (history, content, sources), the history starts **closed** and the composer
  stops being fixed (it would cover the case and the answers).
- **Page header:** a single `h1` with the page name and the "Ocultar/Mostrar historial" button
  (`aria-expanded`, `aria-controls`). The main region is NOT `aria-live` (it would re-read whole answers):
  the current stage is announced with `role="status"`.

### 8.2 History
- A primary "Nueva …" button at the top, search (title and content of messages), list with the most recent first.
- Each item: title (600) and metadata (the user's **local** date and time · number of messages, 0.8125 rem,
  `--text-muted`). The open one: 3 px `--accent` left border and `--bg` background, with `aria-current="page"`.
- Empty: catalog text, no illustration. It lists only the user's own conversations.

### 8.3 Conversation
- **New:** a welcome card (`h2`, explanation, jurisdiction and profile selector). **Existing:** a bar with
  title (`h2`), "Renombrar" and "Eliminar" (inline confirmation, no native dialog) and the **fixed scope** as
  read-only chips ("Ámbito fijado al iniciar; para cambiarlo, inicie una nueva": changing it is a user
  action, never an inference, §4).
- **User turn:** a `--bg` block with a 3 px `--primary` left border, a label ("Su consulta" / "Su argumento")
  and the text as written (`white-space: pre-wrap`, as text).
- **Answer:** a compact `AnswerView` (no visible title or own source list: the panel shows them) or the
  `Refusal` card of §3.2. The marks of §3 do not change.
- **In progress:** a dashed border, the stage (`role="status"`), elapsed time (not announced) and the note on the
  local model's duration. Never an endless spinner (§4).
- **Composer:** a visible label, a character counter, Ctrl/⌘+Enter sends; if the turn fails, the text is
  kept. Fixed at the bottom on wide screens.

### 8.4 Sources panel
A numbered list of the sources cited across the whole conversation (each `cite_id` once, the most recent answer
first), with the chip of §3.1; it opens the viewer of §3.5.

### 8.5 Investigación (file)
Each question is a file (`article`): the label "Pregunta", the question (`h3`) and the full answer with its
numbered list of sources. "Exportar PDF" uses the browser's print: the bar, history, composer and
buttons are hidden; each citation keeps its `citation_string` plus a **note number** that corresponds to the list of sources
(§4, Dossier); one file per page.

### 8.6 Argumentar (case and rounds)
- **Case sheet:** `details` open by default, 3 px `--accent` left border; summary "Caso · {party}";
  facts and claim as text. The party comes from a closed catalog (claimant, defendant, defence, prosecution,
  interested third party). A privacy notice under the form.
- **Round:** the user's argument (8.3) and the answer with the label "Ataque de la contraparte" or "Argumento
  fortalecido". The closing "Vulnerabilidades priorizadas" / "Puntos a reforzar" is taken out of the body and shown as a
  **panel** (3 px `--primary` top border, `--bg` background, **numbered** list, §3.3), with both marks visible.
- **Composer:** "Enviar a la contraparte" (primary) and "Fortalecer mi argumento" (secondary).

## 9. Tools: Leyes · Verificar · Asuntos · Reformas (ADR-017/018/019, 2026-11-08)

The same frame as §8 (bar, tokens, typography, tone); no tool uses the model for legal content.

### 9.0 Navigation and start page
- The bar adds "Leyes", "Verificar" and "Asuntos" after the three workspaces (same style as §8.1). "Reformas" is opened
  from the start page and from the notice.
- Start page: below the three cards, an `h2` "Herramientas" with four cards of the same component. If anything in
  the user's work cites a reformed provision, a notice appears **before** the cards (`--accent-ink` border, 3 px
  `--accent` left border, ◷ icon, `h2` "Hay reformas que afectan su trabajo", number of
  citations and a "Revisar" button). With no alerts, there is no notice (never an empty notice).

### 9.1 Leyes (navigator)
- Two columns (≥ 64 rem): structure (22 rem, `--surface`, with its own scroll) | article. One column below.
- Structure: a law selector (catalog of loaded laws, with their jurisdiction), version and last recorded
  reform, "Ir al artículo" (free number: "47", "50 Bis"; if it does not exist: "Esa ley no tiene ese artículo",
  `role=status`), and the tree as nested `details`/`summary`; articles are buttons with a label (600) and their
  beginning in `--text-muted`. The open one: 3 px `--accent` left border, `--bg` background.
- Article: path (title › chapter), an `h2` with the label (receives focus on opening), **serif** text in a
  `--surface` box with the source's `lang`, "← anterior / siguiente →" and "Partes citables": each part with its
  `citation_string`, the `cite_id` in mono, "No vigente" or "◷ Reformada · fecha" badges (§3.1), "Copiar cita" and
  "Guardar en asunto".
- "Versiones": compare the current text with an earlier one. What was added goes in `<ins>` (2 px underline on
  `--verified-bg`), what was removed in `<del>` (strikethrough on `--unsupported-bg`): shape + colour, never colour alone;
  a legend in text. The URL keeps law and article (`?ley=&p=`).

### 9.2 Verificar (citations of a brief)
- A form in a card: an option group "Pegar el texto" / "Subir un PDF" (radio: one Tab stop, arrows
  inside), name of the brief, text or file, optional matter and the fixed notice **"El escrito no se guarda"**.
  "Cargar un texto de ejemplo" fills the field and does not send.
- Result (an `h2` that receives focus): a summary by status and a **numbered** list of findings. Each status has an
  icon + label + shape, never colour alone: Vigente ✓ (verified style), Derogado / No vigente ◷ (`--accent-ink`),
  No existe ⌕ (dotted `--unsupported`), Ley no cargada ○ (`--text-muted` text), Ley no identificada / ambigua ◇
  (dashed `--reasoning`, "revise"). The verbatim quotation: "coincide" / "NO coincide" / "coincide con una versión
  anterior" / "demasiado corta". A law taken from context is stated as such ("Confírmelo").
- With pasted text (or a freshly read PDF) each finding shows its fragment of the brief with the reference in `<mark>`;
  a saved report has no text (ADR-019) and does not show it. The sources open the viewer of §3.5.
- Footer: "La verificación comprueba existencia, vigencia y coincidencia textual. No juzga la interpretación."

### 9.3 Asuntos
- List: a "Nuevo asunto" form (name, client reference, description, privacy notice) and cards
  (3 px `--primary` top border; title `--primary` 650; reference · role · items · people).
- Open matter (`h2` with focus, 3 px `--accent` left border): actions of the responsible person (close /
  reopen, delete with an inline confirmation saying the conversations are NOT deleted), and `h3` sections:
  Trabajo (links "Nueva en Conversar/Investigación/Argumentar" and "Verificar un escrito", which open the page with
  the matter preselected; a list of conversations with their page, date and "de un colega"), Fuentes guardadas
  (chip §3.1 + reform badge + "Abrir la versión vigente"), Notas, Personas con acceso (add a colleague from the
  firm from a closed list; only the responsible person administers).
- In the three workspaces: an optional "Asunto" selector on creation; in the bar of one's own conversation, a
  compact selector to move it; a colleague's conversation is shown without "Renombrar/Eliminar" and with the note
  "compartida por el asunto". "Guardar en asunto" lives in the source viewer and in Leyes.

### 9.4 Reformas
- `h2` "Su trabajo afectado": each alert with a ◷ badge (change · date), the citation, where it is (conversation,
  saved source, verification; link) and "Abrir lo citado" / "Abrir la versión vigente".
- "Reformas registradas": law, versions, publication date, counts by type of change and "Ver qué cambió".
- "Última revisión de cada fuente": a table (law, checked, result in words). If a source rejects the automatic
  download it says "La fuente rechazó la descarga" (it is not insisted on, ADR-017).
- The source viewer (§3.5) shows, where it applies, "Esta disposición cambió después de citarse (…)" in
  `--accent-ink` with the current version in mono.

### 9.8 Noticias (ADR-027, 2026-11-17)
- A "Noticias" entry at the end of the sidebar tools (§11.1), with a newspaper icon.
- An `h1` header "Noticias", the explanation of how items are chosen (fixed rules, never the model) and "Actualizado: …".
- Filters as buttons with `aria-pressed` (Todas · Cambios a la legislación · Tribunales · Prensa). The active one is
  marked with a border and an `--accent-ink` underline, never with pure `--accent` (§6).
- Each item is a card (`--surface`, `--line` border, `--radius`):
  - first line: a neutral badge of the type, a "Fuente oficial" badge in `--accent-ink` if applicable, outlet and date;
    with no date from the source it says "vista el …", one is never invented;
  - an `h2` title as a link: an external one opens in a new tab with `rel="noreferrer noopener"`, an internal one
    (`/reformas`) navigates within the app;
  - the source's own excerpt, as text; the counts of a reform; "Por qué aparece: …" with the terms.
- No verification colour (§3) is used in news: an item is not a verified citation.
- At the end, an `h2` "Fuentes": a table (source, read, result in words). A source that rejects the reading says
  "La fuente rechazó la lectura" and is not insisted on.

### 9.9 Key-points sheet (ADR-028, 2026-11-17)
- In the matter, an `h3` "Hoja de puntos clave" after "Trabajo del asunto": the explanation (it cites only what the
  matter has already cited; the questions and topics are the assistant's reasoning), the compact model selector (§9.6) and the
  "Generar hoja de puntos clave" button. While it runs, the stage in words (`role="status"`).
- "Última hoja": date · author · status, and "Abrir para imprimir o guardar en PDF". A rejected sheet shows no
  model text: it says why in the system's words (no sources in the matter / could not be verified).
- The sheet (`/asuntos/hoja`) is a letter-size page: the label "HOJA DE PUNTOS CLAVE" in `--accent-ink`, the matter's title,
  "Generada el … por … · modelo …"; a two-column body with the four sections (Puntos clave, Riesgos, Preguntas
  para el cliente, Temas a revisar); citations as superscript `[n]` numbers; reasoning lines in
  italic with ◇ (never looking like a citation); at the foot, numbered "Fuentes citadas", the verification legend and
  "no sustituye el criterio profesional".
- When printing: no sidebar or buttons, white background, 9 pt, 12 mm margins. The browser test generates the
  PDF and requires ONE page.

### 9.6 Model of a conversation (ADR-024, 2026-11-09)
- On creation (Conversar, Investigación, Argumentar): a "Modelo" `fieldset` with one radio card per USABLE model
  (configured on the server and allowed by the firm); hidden if there is only one. Each card: name,
  a location badge ("En este equipo" green §3; "Externo · Anthropic" in `--accent-ink`), "predeterminado"
  if applicable, a line on what it is for and the measurement line: "Medido (fecha, N casos): respondió X de las
  preguntas con fuente · rechazó Y de las que no la tienen · citas válidas · segundos típicos (casos aún sin
  revisión de abogado)" or "Sin medir". Retrieval recall is not shown (it is the same for every model).
  Never "recommended" or stars: the measurement speaks. Chosen card: `--primary` border, `--primary-soft` background.
- An external model shows, BEFORE anything is sent, a notice with a 3 px `--accent` left border: what leaves the
  machine (question, history, fragments; in Argumentar also facts and arguments) and to whom. The case's privacy
  note changes to its "external" version; it never says "no sale de este equipo" if it does leave.
- Open conversation: a "Modelo: …" chip next to the scope (fixed, like jurisdiction and profile); the external one in
  `--accent-ink` with "· externo".
- Ajustes › "Modelos externos": a checkbox "Permitir que el despacho use Claude (Anthropic)" with its explanation;
  only an administrator changes it (others see it disabled with "Solo un administrador…"); if the server
  has no key, it says Claude is unavailable even if allowed.
- Errors in words: "El despacho no permite modelos externos…", "Ese modelo no está configurado…", "El
  proveedor del modelo externo rechazó la solicitud…".

### 9.7 The firm's documents (ADR-025, 2026-11-14)
- An entry in the rail, under Herramientas, after Leyes. A tool page (§9): an upload form
  at the top, the list below.
- Form: file, name (as it appears in citations), type (closed list), language, matter (if any) and
  scope (closed catalog; by default the last scope used). Two sentences that are never omitted: who will see it
  (the whole firm / only the matter's members) and what is stored ("El archivo original no se guarda…").
  The button stays disabled without a file.
- List: one card per document with name, a status badge with shape + icon + text (◷ Procesando ·
  ✓ Listo · N partes citables · ⌕ No se pudo procesar; tones of §9.2, never colour alone), type, matter or
  "Todo el despacho", date and who uploaded it. A failure states the cause in plain language and what to do (e.g.
  scanned PDF → OCR or Word); never an internal key.
- "Ver el texto extraído": the citable parts in order, in the statute typography (§2), with the warning
  to review it before trusting it; each name opens the viewer. "Preguntar sobre este documento" opens Conversar
  in its matter. "Eliminar" (only the uploader or an admin) asks for confirmation on the same card and says
  what is lost.
- Viewer (§11.4) for a firm document: a "Documento del despacho" badge instead of
  Vigente/No vigente and "Sección completa" instead of "Artículo completo".
- While something is processing the list updates itself and the end is announced (`role=status`).

## 10. OPB style (v2, 2026-11-08): how it is applied

Structure reference: Library-RAG (the same product family). Everything lives in section 4 of `globals.css`.
- **Top bar:** a `--nav-bg` band with a 3 px top line in a blue → gold gradient; wordmark in white
  Fraunces with "AI" in `--accent-light` italic; the pages are pills (active: white background at 14 % and
  a 2 px gold underline); **language, theme and "Cerrar sesión" go in the "Ajustes" panel** (a native side dialog,
  gold top border), at any screen width.
- **Headings:** h1/h2 in Fraunces 300 colour `--primary`; the key word in `--accent-ink` italic. Labels
  ("eyebrow"): 11 px, uppercase, wide tracking, `--accent-ink`, preceded by a 24 px gold rule.
- **Start page:** a blue hero with a 48 px grid texture and a gold glow, label, title with italic and
  `#D3E3F4` text; white cards with a `--primary` top border (hover: gold) and a Fraunces title; "Cómo
  funciona" as numbered steps in blue circles.
- **Workspaces:** history and sources in `--surface-2` rails; the active item in `--primary-soft`.
  The user's turn is a **bubble on the right** (`--primary-soft`, 16/4 px corners); the answer reads
  like a document on the left. The composer is a rounded box with a soft shadow and the main button in
  **gold** with `--on-accent` text; the examples are pills.
- **Cards:** `--surface`, `--line` border, 14 px radius, soft shadow; action cards carry a 3 px top border
  (`--primary` or `--accent`). Tables: a blue header with uppercase labels and alternating rows.
- **Footer:** a `--nav-bg` strip, text `#B8C7D8`.
- **Answers (2026-11-09):** the assistant's text is in Plus Jakarta Sans (1 rem, line height 1.7, ≤ 72 ch);
  the serif is kept ONLY for statute text (source viewer, Leyes). This distinguishes at a glance "what
  the law says" from "what the assistant explains".
- **Inline citations:** inside an answer, the citation is a compact sans pill (0.8125 rem, weight 600, ✓,
  `--verified`), without breaking the line (the WCAG 2.5.8 exception for inline targets); in the sources panel
  it keeps the full touch size. The mono `cite_id` stays in the tooltip and in the viewer (§3.1).
- **Dates:** always in the interface language and the user's time zone ("8 nov 2026, 19:31").
- **Matter selector:** appears only if the user has open matters; in the conversation bar it goes in
  one row (label + selector).
- The marks of §3 keep their shape, icon and label; only their colours change to the OPB palette (verified green,
  reasoning purple, unsupported slate).

## 11. Single desk + Studio (v3, 2026-11-13, chosen by the product owner)

Proposals compared on the "Legal AI · Propuestas de UI" canvas (A Estudio, B Expediente, C Mesa única).
Decision: **C for the start page and navigation, A for the answer view**; from B, the verification strip and the
highlighted passage in the viewer. The full B remains as a future "verify answer" mode. It replaces §8.0
(start page), §8.1 (top bar), §8.4 (sources panel) and the parts of §10 that speak of the top bar,
the start page and inline citations; the rest of §8–§10 remains in force.

### 11.1 Side navigation
- A `--nav-bg` rail on the left (16 rem; ≤ 64 rem it moves to the top as a band that wraps). At the top the wordmark;
  then **Inicio**, the "Trabajo" group (Conversar · Investigación · Argumentar, indented, they open their history) and
  the tools (Leyes · Verificar · Asuntos · Reformas); at the foot, "Ajustes" (the dialog of §10).
- Each link ≥ 44 px, a stroke icon + text; active: white background at 12 %, weight 600, `aria-current="page"`.
  There is no rail on `/login`: only the band with the wordmark and "Ajustes".

### 11.2 Start page = a single box
- A blue hero with a 48 px grid: label, an `h1` "¿Qué necesita *resolver* hoy?" and a white card with:
  mode (a radio group as pills: **Preguntar · Investigar a fondo · Argumentar un caso**), the text (its
  label changes with the mode: question / research question / facts of the case), the scope on one line with
  "Cambiar" (§8.0), the matter (if any) and the model in its compact form (selector + the notice of §9.6 if
  external), and the gold button "Preguntar → / Investigar → / Preparar el caso →".
- Sending opens the workspace of that mode and the turn runs **there** (Preguntar, Investigar) or the case form
  is preloaded with the facts (Argumentar: the party is missing, which is chosen there). The text travels in `sessionStorage`,
  once, never in the URL.
- Below: the reform notice (§9.4) and "Continuar donde se quedó": up to 6 cards of recent work of the
  three kinds, each with its label (Pregunta / Investigación / Caso, blue / gold / purple top border),
  title and "N mensajes · fecha".

### 11.3 Answer (Studio)
- A centred reading column (≤ 46 rem); the answer in sans 1.0625 rem, line height 1.75. The user's turn
  stays a bubble on the right (§10).
- **Inline citations = numbers**: a small superscript button (`--accent-ink` on light gold, accessible name
  "Fuente N: {cita}, verificada") that opens the viewer. The number is that of the source in the whole conversation
  (order of first appearance), the same as the panel's. In the file's PDF the citation and its note remain (§8.5).
- **Verification strip** above each answer: "✓ N citas comprobadas contra el texto de la ley" (verified
  green) and, if there are marks of §3, "· M párrafos de análisis propio, sin respaldo normativo". It counts only what
  the system verified; never "N of M claims" (the system does not know how many claims there are).
- Actions under the answer: "Copiar con citas" (text + a numbered list of sources).
- **Sources panel:** numbered cards (number in light gold, citation in `--primary` 600, an excerpt of the text in
  the legal serif, max 4 lines, and "Abrir texto completo"). Investigación keeps its list per file.

### 11.4 Source viewer (from B)
- The cited passage is highlighted with a light-gold background (`--accent` at 22 % over `--surface`) inside the whole article and the viewer
  scrolls to it when opened.
