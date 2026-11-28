// Real-browser checks (system Edge via playwright-core; no browser download).
//   npm run build && npx next start -p 3100   (in another terminal)  then:  node e2e/browser.mjs
// Verifies what unit tests cannot: language switching WITHOUT a reload, theme, keyboard,
// focus visibility, CSP violations, external requests and axe accessibility rules.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3100";
const require = createRequire(import.meta.url);
const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf-8");
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

// A syntactically valid, unexpired JWT-shaped cookie so the proxy lets us into "/".
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const session = `${b64({ alg: "HS256" })}.${b64({ exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`;

const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await context.addCookies([{ name: "session", value: session, url: BASE }]);
// No API on purpose: the shell, i18n, theme and a11y are checked against a stubbed gateway (the
// workspace loads the closed catalogs on mount; without them it shows the catalog error instead).
const J = (id, country, level, state_code, municipality, name_es, name_en = name_es) => ({ id, country, level, state_code, municipality, name_es, name_en });
const STUBS = {
  me: { email: "e2e@demo.local", display_name: "E2E", role: "member", preferred_language: "es-MX" },
  "catalog/jurisdictions": [
    J(1, "MX", "federal", null, null, "Federal (México)", "Federal (Mexico)"),
    J(2, "MX", "state", "NL", null, "Nuevo León"),
    J(3, "MX", "municipal", "NL", "Monterrey", "Monterrey, Nuevo León"),
    J(4, "US", "federal", null, null, "Federal (EE. UU.)", "Federal (USA)"),
    J(5, "US", "state", "CA", null, "California"),
  ],
  "catalog/personas": [{ code: "general", label_es: "General", label_en: "General" }, { code: "labor", label_es: "Laboral", label_en: "Labor" }],
  conversations: [], // ADR-016: an empty history in every workspace
  // features 1, 2, 5, 6 (Brand.md 9): enough for every tool page to render its real states
  laws: [{ official_id: "LFT", title: "Ley Federal del Trabajo", doc_type: "ley", language: "es", version_label: "2026.10.01", valid_from: "2026-10-01", jurisdiction_id: 1, jurisdiction_es: "Federal (México)", jurisdiction_en: "Federal (Mexico)", last_reform: "2026-10-01" }],
  "laws/LFT/toc": [{ path: "d.tsegundo", node_type: "titulo", label: "TITULO SEGUNDO", heading: "Relaciones Individuales de Trabajo", children: [
    { path: "d.tsegundo.civ", node_type: "capitulo", label: "CAPITULO IV", heading: "Rescisión de las relaciones de trabajo", children: [
      { path: "d.tsegundo.civ.art47", node_type: "articulo", label: "Artículo 47", heading: "Son causas de rescisión…", children: [] },
      { path: "d.tsegundo.civ.art48", node_type: "articulo", label: "Artículo 48", heading: "El trabajador podrá solicitar…", children: [] }] }] }],
  "laws/LFT/find": { path: "d.tsegundo.civ.art48", label: "Artículo 48" },
  "laws/LFT/node": { official_id: "LFT", law_title: "Ley Federal del Trabajo", version_label: "2026.10.01", path: "d.tsegundo.civ.art47", node_type: "articulo", label: "Artículo 47",
    text: "Artículo 47.- Son causas de rescisión de la relación de trabajo, sin responsabilidad para el patrón:\n\nII. Incurrir el trabajador en faltas de probidad, honradez o acoso durante sus labores;",
    trail: [{ path: "d.tsegundo", label: "TITULO SEGUNDO" }, { path: "d.tsegundo.civ", label: "CAPITULO IV" }], children: [],
    citables: [{ cite_id: "MX-FED-LFT-ART47-FRACII-v2026.10.01", citation_string: "Ley Federal del Trabajo, art. 47, fracc. II", label: "Fracción II", text: "II. Incurrir…", is_current: true }],
    prev_path: null, next_path: "d.tsegundo.civ.art48",
    reforms: [{ cite_id: "MX-FED-LFT-ART47-FRACII-v2026.10.01", previous_cite_id: "MX-FED-LFT-ART47-FRACII-v2026.05.14", change: "amended", published_on: "2026-10-01", previous_version: "2026.05.14" }] },
  "laws/LFT/history": [
    { document_id: "d1", version_label: "2026.05.14", valid_from: null, valid_to: "2026-10-01", current: false, text: "II. Incurrir el trabajador en faltas de probidad u honradez durante sus labores;" },
    { document_id: "d2", version_label: "2026.10.01", valid_from: "2026-10-01", valid_to: null, current: true, text: "II. Incurrir el trabajador en faltas de probidad, honradez o acoso durante sus labores;" }],
  matters: [{ id: "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", title: "Pérez vs. ACME", client_ref: "EXP-12", description: "", status: "open", role: "owner", members: 2, items: 1, created_at: "2026-11-08T10:00:00Z", updated_at: "2026-11-08T10:00:00Z" }],
  "matters/colleagues": [{ user_id: "u2", display_name: "Mario", email: "mario@demo.local" }],
  "matters/0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b": { matter: { id: "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", title: "Pérez vs. ACME", client_ref: "EXP-12", description: "Despido sin aviso escrito.", status: "open", role: "owner", members: 2, items: 1, created_at: "2026-11-08T10:00:00Z", updated_at: "2026-11-08T10:00:00Z" },
    members: [{ user_id: "u1", display_name: "E2E", email: "e2e@demo.local", role: "owner" }, { user_id: "u3", display_name: "Lucía", email: "lucia@demo.local", role: "member" }],
    notes: [{ id: "n1", body: "Audiencia el 3 de diciembre.", author: "E2E", mine: true, created_at: "2026-11-08T10:00:00Z" }],
    sources: [{ id: "s1", cite_id: "MX-FED-LFT-ART47-FRACII-v2026.05.14", citation_string: "Ley Federal del Trabajo, art. 47, fracc. II", note: "", added_by: "E2E", added_at: "2026-11-08T10:00:00Z", change: "amended", current_cite_id: "MX-FED-LFT-ART47-FRACII-v2026.10.01" }],
    reports: [] },
  "verify/reports": [],
  // ADR-025 (Brand.md 9.7): one file of a matter ready, one being processed, one that failed
  files: [
    { id: "f0000000-0000-4000-8000-000000000001", title: "Contrato de servicios", kind: "contrato", language: "es", jurisdiction_id: 1, matter_id: "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", status: "ready", error_key: null, pages: null, chunk_count: 7, source_format: "docx", created_at: "2026-11-14T10:00:00Z", mine: true, uploaded_by_name: "E2E", can_delete: true, ocr_pages: [], ocr_excluded_pages: [], ocr_confidence: null, amount_mismatch_units: [] },
    { id: "f0000000-0000-4000-8000-000000000002", title: "Demanda inicial", kind: "escrito", language: "es", jurisdiction_id: 1, matter_id: null, status: "failed", error_key: "no_text", pages: null, chunk_count: null, source_format: "pdf", created_at: "2026-11-14T09:00:00Z", mine: false, uploaded_by_name: "Mario", can_delete: false, ocr_pages: [], ocr_excluded_pages: [], ocr_confidence: null, amount_mismatch_units: [] },
  ],
  "files/f0000000-0000-4000-8000-000000000001/text": [
    { cite_id: "CF-D1A2B3C4D5E-S4", citation_string: "«Contrato de servicios», PRIMERA.- OBJETO", text: "PRIMERA.- OBJETO. La Prestadora se obliga a prestar servicios de asesoría jurídica. [texto de ejemplo]" },
    { cite_id: "CF-D1A2B3C4D5E-S7", citation_string: "«Contrato de servicios», CUARTA.- PENA CONVENCIONAL", text: "CUARTA.- PENA CONVENCIONAL. Tres meses de honorarios. [texto de ejemplo]" },
  ],
  reforms: { changes: [{ id: "c1", official_id: "LFT", title: "Ley Federal del Trabajo", old_version: "2026.05.14", new_version: "2026.10.01", published_on: "2026-10-01", detected_at: "2026-11-08T09:00:00Z", counts: { amended: 2, added: 2, derogated: 1, removed: 1, unchanged: 30 } }],
    checks: [{ official_id: "LFT", checked_at: "2026-11-08T09:00:00Z", outcome: "ingested", http_status: 200, detail: null }, { official_id: "CC_NL", checked_at: "2026-11-08T09:00:00Z", outcome: "skipped", http_status: null, detail: "inbox" }] },
  // ADR-027: the shared legal news feed (sample data)
  news: { refreshed_at: "2026-11-17T09:00:00Z", items: [
    { id: "n1", kind: "court", title: "La Suprema Corte resuelve un amparo en revisión [ejemplo]", url: "https://www.internet2.scjn.gob.mx/red2/comunicados/comunicado.asp?id=1", outlet: "Suprema Corte de Justicia de la Nación · Comunicado 1", official: true, published_at: null, first_seen_at: "2026-11-17T09:00:00Z", excerpt: null, terms: ["Suprema Corte", "amparo"], facts: {}, score: 6.5 },
    { id: "n2", kind: "reform", title: "Ley Federal del Trabajo", url: "/reformas?c=c1", outlet: "Vigilante de reformas", official: true, published_at: "2026-10-01T12:00:00Z", first_seen_at: "2026-11-08T09:00:00Z", excerpt: null, terms: ["ley"], facts: { counts: { amended: 2, added: 2 } }, score: 5.1 },
    { id: "n3", kind: "press", title: "Decidirá la Corte si atrae un litigio [ejemplo]", url: "https://www.jornada.com.mx/ejemplo", outlet: "La Jornada", official: false, published_at: "2026-11-16T08:00:00Z", first_seen_at: "2026-11-17T09:00:00Z", excerpt: "Extracto publicado por la fuente. [texto de ejemplo]", terms: ["Suprema Corte", "juicio"], facts: {}, score: 4.2 },
  ], sources: [
    { source_id: "scjn", outlet: "Suprema Corte de Justicia de la Nación", official: true, checked_at: "2026-11-17T09:00:00Z", outcome: "collected" },
    { source_id: "jornada_politica", outlet: "La Jornada", official: false, checked_at: "2026-11-17T09:00:00Z", outcome: "blocked" },
  ] },
  // ADR-028: the key-points sheet of the sample matter
  "matters/0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b/cheatsheets": [{"id":"5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b","matter_id":"0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b","created_at":"2026-11-17T10:00:00Z","created_by_name":"Ana","language":"es","model_profile":"gemma","status":"answered","content":"## PUNTOS CLAVE\n- La falta de probidad u honradez del trabajador es causa de rescisión sin responsabilidad para el patrón [cite: MX-FED-LFT-ART47-FRACII-v2026.10.01].\n- El trabajador puede pedir la reinstalación o la indemnización de tres meses de salario [cite: MX-FED-LFT-ART48-v2026.10.01].\n- La causa debe acreditarse con hechos concretos ocurridos durante las labores [cite: MX-FED-LFT-ART47-FRACII-v2026.10.01].\n## RIESGOS\n- La contraparte sostendrá que el despido fue injustificado y pedirá la reinstalación [cite: MX-FED-LFT-ART48-v2026.10.01].\n- [razonamiento estratégico] No hay constancia escrita del aviso de rescisión entregado al trabajador.\n## PREGUNTAS PARA EL CLIENTE\n- [razonamiento estratégico] ¿En qué fecha exacta ocurrió la falta y quién la presenció?\n- [razonamiento estratégico] ¿Se entregó al trabajador el aviso por escrito con la causa del despido?\n- [razonamiento estratégico] ¿Existen registros de asistencia, correos o videos de ese día?\n## TEMAS A REVISAR\n- [razonamiento estratégico] Confirmar el plazo de prescripción para la acción del trabajador.\n- [razonamiento estratégico] Reunir recibos de nómina y el contrato individual de trabajo.","citations":[{"cite_id":"MX-FED-LFT-ART47-FRACII-v2026.10.01","citation_string":"Ley Federal del Trabajo, art. 47, fracc. II"},{"cite_id":"MX-FED-LFT-ART48-v2026.10.01","citation_string":"Ley Federal del Trabajo, art. 48"}],"material":{"conversations":2,"notes":1,"saved_sources":1,"sources":2,"material_chars":3000}}],
  "matters/0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b/cheatsheets/5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b": {"id":"5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b","matter_id":"0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b","created_at":"2026-11-17T10:00:00Z","created_by_name":"Ana","language":"es","model_profile":"gemma","status":"answered","content":"## PUNTOS CLAVE\n- La falta de probidad u honradez del trabajador es causa de rescisión sin responsabilidad para el patrón [cite: MX-FED-LFT-ART47-FRACII-v2026.10.01].\n- El trabajador puede pedir la reinstalación o la indemnización de tres meses de salario [cite: MX-FED-LFT-ART48-v2026.10.01].\n- La causa debe acreditarse con hechos concretos ocurridos durante las labores [cite: MX-FED-LFT-ART47-FRACII-v2026.10.01].\n## RIESGOS\n- La contraparte sostendrá que el despido fue injustificado y pedirá la reinstalación [cite: MX-FED-LFT-ART48-v2026.10.01].\n- [razonamiento estratégico] No hay constancia escrita del aviso de rescisión entregado al trabajador.\n## PREGUNTAS PARA EL CLIENTE\n- [razonamiento estratégico] ¿En qué fecha exacta ocurrió la falta y quién la presenció?\n- [razonamiento estratégico] ¿Se entregó al trabajador el aviso por escrito con la causa del despido?\n- [razonamiento estratégico] ¿Existen registros de asistencia, correos o videos de ese día?\n## TEMAS A REVISAR\n- [razonamiento estratégico] Confirmar el plazo de prescripción para la acción del trabajador.\n- [razonamiento estratégico] Reunir recibos de nómina y el contrato individual de trabajo.","citations":[{"cite_id":"MX-FED-LFT-ART47-FRACII-v2026.10.01","citation_string":"Ley Federal del Trabajo, art. 47, fracc. II"},{"cite_id":"MX-FED-LFT-ART48-v2026.10.01","citation_string":"Ley Federal del Trabajo, art. 48"}],"material":{"conversations":2,"notes":1,"saved_sources":1,"sources":2,"material_chars":3000}},
  "reforms/alerts": [{ where: "matter_source", target_id: "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", target_title: "Pérez vs. ACME", kind: null, matter_id: "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", cite_id: "MX-FED-LFT-ART47-FRACII-v2026.05.14", citation_string: "Ley Federal del Trabajo, art. 47, fracc. II", change: "amended", published_on: "2026-10-01", current_cite_id: "MX-FED-LFT-ART47-FRACII-v2026.10.01" }],
};
const POSTS = {
  verify: { report_id: "r1", title: "Demanda", summary: { total: 3, vigente: 1, no_vigente: 1, fuera_del_corpus: 1, quote_coincide: 1 }, text: null, warnings: [], findings: [
    { ref: "art. 47, fracc. II, Ley Federal del Trabajo", official_id: "LFT", status: "vigente", quote: "coincide", cite_ids: ["MX-FED-LFT-ART47-FRACII-v2026.10.01"], citation_string: "Ley Federal del Trabajo, art. 47, fracc. II", reform: null, span: [17, 37], inferred: false },
    { ref: "art. 49, Ley Federal del Trabajo", official_id: "LFT", status: "no_vigente", quote: null, cite_ids: [], citation_string: null, reform: { change: "removed", published_on: "2026-10-01" }, span: [120, 131], inferred: true },
    { ref: "art. 21, Ley del Seguro Social", official_id: null, status: "fuera_del_corpus", quote: null, cite_ids: [], citation_string: null, reform: null, span: [200, 211], inferred: false }] },
};
await context.route("**/api/v1/**", (route) => {
  const path = new URL(route.request().url()).pathname.replace(/^\/api\/v1\//, "");
  const method = route.request().method();
  if (method === "GET" && path in STUBS) return route.fulfill({ json: STUBS[path] });
  if (method === "POST" && path in POSTS) return route.fulfill({ json: POSTS[path] });
  return route.fulfill({ status: 503, json: { detail: "service unavailable", code: "api_down" } });
});
const page = await context.newPage();

const external = [];
const cspErrors = [];
const consoleErrors = [];
page.on("request", (r) => {
  const u = new URL(r.url());
  if (!["localhost", "127.0.0.1"].includes(u.hostname) && u.protocol.startsWith("http")) external.push(r.url());
});
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
  if (/Content Security Policy/i.test(m.text())) cspErrors.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(String(e)));

// ---- home, default locale ------------------------------------------------------------------
await page.goto(`${BASE}/`);
const welcome = () => page.locator(".welcome h2").innerText();
await page.locator(".mesa").waitFor();
check("the start page is one box with three modes, in es-MX by default (Brand.md 11.2)", (await page.locator(".mesa-mode").count()) === 3 && (await page.locator("h1").innerText()).includes("resolver hoy"));
check("the start page says it answers only from the loaded laws", (await page.locator(".home-trust").innerText()).includes("cita cada fuente"));
// `.recent` renders its title at once and the empty message only after the request resolves: wait
// for the message, or a slow runner reads the section before it is there (failed on CI only).
check("recent work says when there is none yet", await page.locator(".recent", { hasText: "trabajo reciente" }).waitFor({ timeout: 10_000 }).then(() => true, () => false));
check("an amendment that touches the user's work raises a banner on the start page", (await page.locator(".alert-banner").innerText()).includes("1 citas"));
check("html lang is es-MX", (await page.getAttribute("html", "lang")) === "es-MX");
await page.getByLabel("Argumentar un caso").check();
check("the box relabels itself for the chosen mode", (await page.locator(".mesa-label").innerText()) === "Hechos del caso");
await page.getByLabel("Hechos del caso").fill("La trabajadora fue despedida sin aviso escrito.");
await page.getByRole("button", { name: "Preparar el caso →" }).click();
await page.waitForURL(/\/argumentar$/);
await page.locator(".case-form").waitFor();
check("the case facts written on the start page prefill the case form (never the URL)", (await page.getByLabel("Hechos", { exact: true }).inputValue()).includes("sin aviso escrito") && !page.url().includes("aviso"));
await page.goto(`${BASE}/conversar`);
await page.locator(".welcome").waitFor();
check("a workspace opens with its welcome", (await welcome()).includes("consultar") && (await page.locator("h1").innerText()) === "Conversar");
check("exactly one h1 on the workspace", (await page.locator("h1").count()) === 1);

// ---- minimal configuration (non-technical users) ------------------------------------------------
// a <select> inside its <label> adds the chosen option to its accessible name: find it by the label text
const stateSelect = page.locator("label.field").filter({ has: page.locator("span", { hasText: /^Estado$/ }) }).locator("select");
check("the scope starts as one line with a default, selectors folded", (await page.locator(".scope-current").innerText()).includes("Federal (México)") && !(await stateSelect.isVisible()));
await page.getByRole("button", { name: "Cambiar" }).click();
check("'Cambiar' unfolds the selectors and says so (aria-expanded)", (await stateSelect.isVisible()) && (await page.getByRole("button", { name: "Listo" }).getAttribute("aria-expanded")) === "true");
check("the profile and vigencia options explain themselves", (await page.locator(".scope-picker").innerText()).includes("no amplía ni restringe"));
await page.getByRole("button", { name: "Listo" }).click();
const example = page.locator(".example").first();
const exampleText = await example.innerText();
await example.click();
await page.waitForFunction(() => (document.querySelector(".composer textarea")?.value ?? "").length > 0, null, { timeout: 5000 }).catch(() => {});
const filled = await page.locator(".composer textarea").inputValue();
check("an example fills the composer (and sends nothing)", filled.trim() === exampleText.trim() && (await page.locator(".turn").count()) === 0, JSON.stringify(filled.slice(0, 40)));
check("the composer moves the focus to itself and tells the shortcut", (await page.evaluate(() => document.activeElement?.tagName)) === "TEXTAREA" && (await page.locator(".composer").innerText()).includes("Ctrl + Enter"));
await page.getByLabel("Mensaje").fill("");

// ---- the three workspaces (ADR-016, Brand.md 8) ----------------------------------------------
const nav = page.getByRole("navigation", { name: "Secciones" });
check("the side rail links home, the three workspaces and the tools (Brand.md 11.1)", (await nav.getByRole("link").allInnerTexts()).join("|") === "Inicio|Conversar|Investigación|Argumentar|Leyes|Documentos|Verificar|Asuntos|Reformas|Noticias");
check("the open workspace is aria-current", (await nav.getByRole("link", { name: "Conversar" }).getAttribute("aria-current")) === "page");
check("an empty history says so", (await page.locator(".history").innerText()).includes("Aún no hay conversaciones"));
await nav.getByRole("link", { name: "Investigación" }).click();
await page.waitForURL(/\/investigacion$/);
await page.locator("h1", { hasText: "Investigación" }).waitFor();
await page.getByRole("button", { name: "Investigar" }).waitFor({ timeout: 10_000 }).catch(() => {}); // the welcome renders after the catalogs load
check("Investigación renders its own page", (await page.locator("h1").innerText()) === "Investigación" && (await page.getByRole("button", { name: "Investigar" }).count()) === 1);
await nav.getByRole("link", { name: "Argumentar" }).click();
await page.waitForURL(/\/argumentar$/);
await page.locator(".case-form").waitFor();
check("Argumentar opens the new-case form with the closed list of sides", (await page.getByLabel("Parte que representa").locator("option").count()) === 5);
check("a case cannot be created without facts", await page.getByRole("button", { name: "Crear caso" }).isDisabled());
const histToggle = page.getByRole("button", { name: "Ocultar historial" });
await histToggle.click();
check("the history can be hidden", (await page.getByRole("button", { name: "Mostrar historial" }).getAttribute("aria-expanded")) === "false" && !(await page.locator(".history").isVisible()));
await page.getByRole("button", { name: "Mostrar historial" }).click();
check("a new case can be kept in a matter (optional)", (await page.getByLabel("Asunto").locator("option").allInnerTexts()).join("|") === "Sin asunto|Pérez vs. ACME · EXP-12");

// ---- the tools (Brand.md 9): Leyes, Verificar, Asuntos, Reformas ---------------------------
await nav.getByRole("link", { name: "Leyes" }).click();
await page.waitForURL(/\/leyes/);
await page.locator(".toc").first().waitFor();
check("Leyes lists the loaded laws and the last recorded amendment", (await page.locator(".laws-nav").innerText()).includes("2026-10-01"));
await page.locator(".toc summary", { hasText: "TITULO SEGUNDO" }).click();
await page.locator(".toc summary", { hasText: "CAPITULO IV" }).click();
await page.locator(".toc-leaf", { hasText: "Artículo 47" }).click();
await page.locator("#law-node-title").waitFor();
check("an article opens with its location, its text as legal text and the focus on its title",
  (await page.locator(".trail").innerText()).includes("CAPITULO IV") && (await page.locator(".law-text").getAttribute("lang")) === "es"
  && (await page.evaluate(() => document.activeElement?.id)) === "law-node-title");
check("a citable part shows its amendment with text, not colour alone", (await page.locator(".citable").innerText()).includes("Reformada"));
await page.getByRole("button", { name: "Comparar con versiones anteriores" }).click();
await page.locator(".law-diff").waitFor();
check("the comparison marks what the amendment added (<ins>) and removed (<del>)", (await page.locator(".law-diff ins").count()) > 0 && (await page.locator(".law-diff del").count()) > 0);
check("the URL keeps the law and the article (shareable, survives reload)", page.url().includes("ley=LFT") && page.url().includes("p=d.tsegundo.civ.art47"));
await nav.getByRole("link", { name: "Verificar" }).click();
await page.waitForURL(/\/verificar/);
check("Verificar promises the brief is not stored", (await page.locator(".verify-form").innerText()).includes("El escrito no se guarda"));
await page.getByRole("button", { name: "Cargar un texto de ejemplo" }).click();
await page.getByRole("button", { name: "Verificar", exact: true }).click();
await page.locator(".findings").waitFor();
const findingsText = await page.locator(".findings").innerText();
check("each finding says its status in words (Vigente / No vigente / Ley no cargada)", ["Vigente", "No vigente", "Ley no cargada"].every((x) => findingsText.includes(x)));
check("a law taken from context is flagged for the lawyer to confirm", findingsText.includes("Confírmelo"));
check("the result receives the focus", (await page.evaluate(() => document.activeElement?.id)) === "verify-result");
await page.getByLabel("Subir un PDF").focus();
await page.keyboard.press("ArrowLeft");
check("the input mode is a radio group operable with the arrow keys", await page.getByLabel("Pegar el texto").isChecked());
await nav.getByRole("link", { name: "Asuntos" }).click();
await page.waitForURL(/\/asuntos/);
await page.locator(".matter-card").first().click();
await page.locator("#matter-title").waitFor();
check("a matter shows its saved sources with the amendment badge", (await page.locator(".matter").innerText()).includes("Reformada"));
check("the owner can add a colleague of the firm (closed list)", (await page.getByLabel("Agregar a un colega del despacho").locator("option").count()) === 2);
check("work starts inside the matter from its own links", (await page.getByRole("link", { name: "Nueva en Conversar" }).getAttribute("href")).includes("matter="));
await page.goto(`${BASE}/reformas`);
await page.locator(".alert-list").waitFor();
check("Reformas lists the affected work and every source check", (await page.locator(".alert-list").innerText()).includes("Pérez vs. ACME") && (await page.locator(".checks tbody tr").count()) === 2);
await page.goto(`${BASE}/noticias`);
await page.locator(".news-list").waitFor();
check("Noticias lists the feed with its kind, source and why it is there", (await page.locator(".news-item").count()) === 3 && (await page.locator(".news-list").innerText()).includes("Por qué aparece"));
check("an external news link opens without a referrer", (await page.locator('.news-item a[href^="https://"]').evaluateAll((as) => as.every((a) => a.rel.includes("noreferrer") && a.target === "_blank"))));
await page.getByRole("button", { name: "Prensa" }).click();
check("the news filter shows only the chosen kind", (await page.locator(".news-item").count()) === 1);
check("a refused news source is said in words", (await page.locator("table.checks").innerText()).includes("La fuente rechazó la lectura"));
await page.goto(`${BASE}/asuntos?m=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b`);
await page.locator(".sheet-latest").waitFor();
check("a matter offers its key-points sheet and links the latest one", (await page.locator(".sheet-section").innerText()).includes("Generar hoja de puntos clave") && (await page.getByRole("link", { name: "Abrir para imprimir o guardar en PDF" }).count()) === 1);
await page.goto(`${BASE}/asuntos/hoja?m=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b&h=5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b`);
await page.locator(".sheet").waitFor();
check("the sheet shows its four sections, numbered citations and the cited sources", (await page.locator(".sheet-block").count()) === 4 && (await page.locator(".sheet-cite").first().innerText()) === "[1]" && (await page.locator(".sheet-foot ol li").count()) === 2);
check("reasoning lines are marked, never styled as citations", (await page.locator(".sheet-reasoning").count()) === 6 && (await page.locator(".sheet-reasoning .sheet-cite").count()) === 0);
{
  const pdf = await page.pdf({ format: "Letter", printBackground: false });
  const pages = (Buffer.from(pdf).toString("latin1").match(/\/Type\s*\/Page[^s]/g) || []).length;
  check("the sheet prints on ONE letter page", pages === 1, `${pages} pages`);
}
await page.goto(`${BASE}/conversar`);

// Language, theme and sign-out live in the "Ajustes" side panel (Brand.md 10): open it, choose, close.
async function pick(option) {
  await page.getByRole("button", { name: /^(Ajustes|Settings)$/ }).click();
  await page.locator("dialog.settings-dialog[open]").waitFor();
  await page.locator("dialog.settings-dialog").getByRole("button", { name: option, exact: true }).click();
  await page.keyboard.press("Escape");
  await page.locator("dialog.settings-dialog[open]").waitFor({ state: "detached" }).catch(() => {});
}

// ---- language toggle without reload ---------------------------------------------------------
await page.evaluate(() => {
  window.__marker = "same-document";
});
let navigations = 0;
page.on("framenavigated", () => navigations++);
await pick("English");
await page.waitForFunction(() => document.documentElement.lang === "en-US");
check("language switches to en-US in place", (await welcome()).includes("look up") && (await page.locator("h1").innerText()) === "Chat");
check("no reload happened (document marker and navigation count)", (await page.evaluate(() => window.__marker)) === "same-document" && navigations === 0);
check("footer disclaimer follows the language", (await page.locator("footer").innerText()).includes("Research tool"));
await page.getByRole("button", { name: "Settings" }).click();
check("aria-pressed reflects the active language (and the panel follows the language)", (await page.locator("dialog.settings-dialog").getByRole("button", { name: "English" }).getAttribute("aria-pressed")) === "true");
await page.keyboard.press("Escape");
await page.reload();
check("locale persists across reload (cookie)", (await page.getAttribute("html", "lang")) === "en-US" && (await welcome()).includes("look up"));
await pick("Español");
await page.waitForFunction(() => document.documentElement.lang === "es-MX");

// ---- theme ------------------------------------------------------------------------------------
const bgOf = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const light = await bgOf();
await pick("Oscuro");
await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
const dark = await bgOf();
check("dark theme changes the page background to the Brand.md token", light === "rgb(244, 246, 249)" && dark === "rgb(6, 26, 47)", `${light} -> ${dark}`);
await page.reload();
check("theme persists across reload without a flash (SSR from cookie)", (await page.getAttribute("html", "data-theme")) === "dark");
await pick("Claro");

// ---- fonts ------------------------------------------------------------------------------------
const fonts = await page.evaluate(async () => {
  await document.fonts.ready;
  return [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replaceAll('"', ""));
});
check("self-hosted OPB fonts actually load (Plus Jakarta Sans for UI, Fraunces for titles)", fonts.includes("Plus Jakarta Sans") && fonts.includes("Fraunces"), fonts.join(", "));

// ---- keyboard and focus -----------------------------------------------------------------------
await page.goto(`${BASE}/conversar`);
await page.keyboard.press("Tab");
check("first Tab reaches the skip link", await page.evaluate(() => document.activeElement?.classList.contains("skip-link")));
const skipVisible = await page.evaluate(() => document.activeElement.getBoundingClientRect().top >= 0);
check("skip link is visible when focused", skipVisible);
const outline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle + " " + getComputedStyle(document.activeElement).outlineWidth);
check("focused control has a visible outline", outline.startsWith("solid") && outline.endsWith("2px"), outline);
const tabStops = [];
for (let i = 0; i < 12; i++) {
  await page.keyboard.press("Tab");
  tabStops.push(await page.evaluate(() => document.activeElement?.textContent?.trim()));
}
check("the settings button is reachable by keyboard from the side rail", tabStops.includes("Ajustes"), tabStops.slice(0, 12).join(" | "));
await page.getByRole("button", { name: "Ajustes" }).focus();
await page.keyboard.press("Enter");
await page.locator("dialog.settings-dialog[open]").waitFor();
const inPanel = [];
for (let i = 0; i < 8; i++) {
  inPanel.push(await page.evaluate(() => document.activeElement?.textContent?.trim()));
  await page.keyboard.press("Tab");
}
check("the settings panel holds every option and keeps the focus inside (native dialog)", ["Español", "English", "Sistema", "Claro", "Oscuro"].every((n) => inPanel.includes(n)), inPanel.join(" | "));
await page.locator("dialog.settings-dialog").getByRole("button", { name: "English" }).focus();
await page.keyboard.press("Enter");
check("a toggle can be activated with the keyboard", (await page.getAttribute("html", "lang")) === "en-US");
await page.keyboard.press("Escape");
check("Escape closes the settings panel", (await page.locator("dialog.settings-dialog[open]").count()) === 0);
await pick("Español");

// ---- every control is reachable with the keyboard alone ---------------------------------------
async function tabReachesEverything(path, label) {
  await page.goto(`${BASE}${path}`);
  await page.locator("main h1").waitFor();
  await page.waitForTimeout(300);
  const total = await page.evaluate(() => {
    // a radio group is ONE Tab stop (arrows move inside it): only its checked radio is counted
    const els = [...document.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]):not([type='radio']:not(:checked)), select:not([disabled]), textarea:not([disabled]), summary, [tabindex='0']")]
      .filter((e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden");
    els.forEach((e, i) => e.setAttribute("data-kb", String(i)));
    return els.length;
  });
  const seen = new Set();
  for (let i = 0; i < total + 15; i++) {
    await page.keyboard.press("Tab");
    const k = await page.evaluate(() => document.activeElement?.getAttribute("data-kb"));
    if (k !== null && k !== undefined) seen.add(k);
  }
  check(`keyboard: every control on ${label} is reachable with Tab`, seen.size === total, `${seen.size}/${total}`);
}
await tabReachesEverything("/", "the start page");
await tabReachesEverything("/conversar", "Conversar");
await tabReachesEverything("/investigacion", "Investigación");
await tabReachesEverything("/argumentar", "Argumentar");
await tabReachesEverything("/leyes?ley=LFT&p=d.tsegundo.civ.art47", "Leyes");
await tabReachesEverything("/verificar", "Verificar");
await tabReachesEverything(`/asuntos?m=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b`, "Asuntos");
await tabReachesEverything("/reformas", "Reformas");
await tabReachesEverything("/noticias", "Noticias");
await tabReachesEverything("/asuntos/hoja?m=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b&h=5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", "the key-points sheet");
await page.goto(`${BASE}/conversar`);

// ---- phone width: the rail becomes a band that stays small, settings behind one button (11.1) --
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${BASE}/conversar`);
await page.locator(".welcome").waitFor();
const barHeight = await page.locator(".app-nav").evaluate((e) => e.getBoundingClientRect().height);
check("on a phone the navigation band takes less than a third of the screen", barHeight < 844 / 3, `${Math.round(barHeight)}px`);
check("on a phone the history starts closed", (await page.getByRole("button", { name: "Mostrar historial" }).count()) === 1);
const settings = page.getByRole("button", { name: "Ajustes" });
check("on a phone language and theme stay behind 'Ajustes'", !(await page.getByRole("button", { name: "English" }).isVisible()));
await settings.click();
check("'Ajustes' reveals them", await page.getByRole("button", { name: "English" }).isVisible());
await page.addScriptTag({ content: axeSource });
const phoneAxe = await page.evaluate(async () => await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"] }));
check("axe: no violations at phone width", phoneAxe.violations.length === 0, phoneAxe.violations.map((v) => `${v.id}(${v.nodes.length})`).join(", "));
const sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
check("no sideways scrolling at phone width", !sideways);
await page.setViewportSize({ width: 1280, height: 800 });
await page.goto(`${BASE}/conversar`);

// ---- target size (WCAG 2.5.5 as per Brand.md section 6: >= 44px) ---------------------------
// A checkbox's target is its wrapping <label> (clicking it toggles the box), so measure that.
const small = await page.evaluate(() => [...document.querySelectorAll("button, a.wordmark, input")].filter((e) => !e.closest(".answer")) /* inline citations: WCAG 2.5.8 inline exception (Brand.md 10) */.map((e) => [e.textContent?.trim() || e.name || e.type, ((e.type === "checkbox" || e.type === "radio") && e.closest("label") ? e.closest("label") : e).getBoundingClientRect()]).filter(([, r]) => r.height < 44 && r.width > 0).map(([n, r]) => `${n}:${Math.round(r.height)}px`));
check("interactive controls are at least 44px tall", small.length === 0, small.join(", "));

// ---- axe, both themes and both pages ---------------------------------------------------------
async function axe(label) {
  await page.addScriptTag({ content: axeSource });
  const r = await page.evaluate(async () => await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"] }));
  check(`axe: no violations on ${label}`, r.violations.length === 0, r.violations.map((v) => `${v.id}(${v.nodes.length})`).join(", "));
}
await page.goto(`${BASE}/`);
await axe("start page (light)");
for (const p of ["conversar", "investigacion", "argumentar"]) {
  await page.goto(`${BASE}/${p}`);
  await page.locator(".welcome").waitFor();
  await axe(`${p} (light)`);
  await pick("Oscuro");
  await axe(`${p} (dark)`);
  await pick("Claro");
}
for (const [p, ready] of [["leyes?ley=LFT&p=d.tsegundo.civ.art47", "#law-node-title"], ["verificar", ".verify-form"], ["documentos", ".file-list"], ["asuntos?m=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", "#matter-title"], ["reformas", ".alert-list"], ["noticias", ".news-list"], ["asuntos/hoja?m=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b&h=5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", ".sheet"]]) {
  await page.goto(`${BASE}/${p}`);
  await page.locator(ready).waitFor();
  await axe(`${p.split("?")[0]} (light)`);
  await pick("Oscuro");
  await axe(`${p.split("?")[0]} (dark)`);
  await pick("Claro");
}
await page.goto(`${BASE}/login`);
await axe("login (light)");
await pick("Oscuro");
await axe("login (dark)");
await pick("Sistema");

// ---- firm documents (ADR-025, Brand.md 9.7) ----------------------------------------------------
await page.goto(`${BASE}/documentos?matter=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b`);
await page.locator(".file-item").first().waitFor();
check("Documentos: the upload form says what is kept and who will see it", (await page.locator(".file-form").innerText()).includes("El archivo original no se guarda") && (await page.locator(".file-form").innerText()).includes("solo lo verán los miembros del asunto"));
check("Documentos: nothing can be sent without a file", await page.getByRole("button", { name: "Subir y procesar" }).isDisabled());
check("Documentos: the matter of the link is preselected", (await page.locator(".file-form").getByLabel("Asunto").inputValue()) === "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b");
const fileRows = page.locator(".file-item");
check("Documentos: a ready file shows its citable parts and its matter", (await fileRows.nth(0).innerText()).includes("Listo · 7 partes citables") && (await fileRows.nth(0).innerText()).includes("Pérez vs. ACME"));
check("Documentos: a failed file says why in plain words and what to do", (await fileRows.nth(1).innerText()).includes("No se pudo procesar") && (await fileRows.nth(1).innerText()).includes("PDF escaneado"));
check("Documentos: only the uploader (or an admin) gets the delete button", (await fileRows.nth(0).getByRole("button", { name: "Eliminar" }).count()) === 1 && (await fileRows.nth(1).getByRole("button", { name: "Eliminar" }).count()) === 0);
await fileRows.nth(0).getByRole("button", { name: "Ver el texto extraído" }).click();
await page.locator(".file-units li").first().waitFor();
check("Documentos: the extracted text is shown part by part, as text", (await page.locator(".file-units li").count()) === 2 && (await page.locator(".file-unit-text").first().innerText()).startsWith("PRIMERA.- OBJETO"));
check("Documentos: 'Preguntar sobre este documento' opens a conversation in its matter", (await fileRows.nth(0).getByRole("link", { name: "Preguntar sobre este documento" }).getAttribute("href")) === "/conversar?matter=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b");
await fileRows.nth(0).getByRole("button", { name: "Eliminar" }).click();
check("Documentos: deleting asks first and says what is lost", (await fileRows.nth(0).innerText()).includes("¿Eliminar el documento? Se borra su texto"));
await fileRows.nth(0).getByRole("button", { name: "Cancelar" }).click();

// ---- login form behaviour (API is down on purpose) --------------------------------------------
await page.goto(`${BASE}/login`);
await page.getByLabel("Despacho").fill("despacho-demo");
await page.getByLabel("Correo electrónico").fill("abogada@despacho.mx");
await page.getByLabel("Contraseña", { exact: true }).fill("secreto");
await page.getByRole("button", { name: "Entrar" }).click();
// Next injects its own empty role=alert route announcer: scope to the form message.
await page.locator(".form-status").waitFor();
check("API down shows an unavailable message, not a crash", (await page.locator(".form-status").innerText()).includes("no está disponible"));
check("the message is announced to assistive tech (role=alert)", (await page.locator(".form-status").getAttribute("role")) === "alert");
check("the submit button recovers after the error", await page.getByRole("button", { name: "Entrar" }).isEnabled());
check("the password is never put in the URL or the DOM text", !page.url().includes("secreto") && !(await page.content()).includes("secreto"));
await page.getByLabel("Mostrar contraseña").check();
check("'Mostrar contraseña' reveals what was typed", (await page.getByLabel("Contraseña", { exact: true }).getAttribute("type")) === "text");
check("the login says whom to ask for access", (await page.locator(".login-card").innerText()).includes("administradora de su despacho"));

// ---- protection ------------------------------------------------------------------------------
const anon = await browser.newContext();
const anonPage = await anon.newPage();
await anonPage.goto(`${BASE}/`);
check("without a session, / redirects to /login", new URL(anonPage.url()).pathname === "/login");
await anon.close();

check("no request left localhost", external.length === 0, external.join(", "));
check("no CSP violation reported by the browser", cspErrors.length === 0, cspErrors.join(" | ").slice(0, 200));
const realErrors = consoleErrors.filter((e) => !/Failed to load resource.*(503|404)/.test(e));
check("no unexpected console errors", realErrors.length === 0, realErrors.join(" | ").slice(0, 300));

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
