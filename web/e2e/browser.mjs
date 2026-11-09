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
  reforms: { changes: [{ id: "c1", official_id: "LFT", title: "Ley Federal del Trabajo", old_version: "2026.05.14", new_version: "2026.10.01", published_on: "2026-10-01", detected_at: "2026-11-08T09:00:00Z", counts: { amended: 2, added: 2, derogated: 1, removed: 1, unchanged: 30 } }],
    checks: [{ official_id: "LFT", checked_at: "2026-11-08T09:00:00Z", outcome: "ingested", http_status: 200, detail: null }, { official_id: "CC_NL", checked_at: "2026-11-08T09:00:00Z", outcome: "skipped", http_status: null, detail: "inbox" }] },
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
check("the start page explains the three workspaces, in es-MX by default", (await page.locator(".home-tiles:not(.home-tools) .home-tile").count()) === 3 && (await page.locator("h1").innerText()).includes("hacer hoy"));
check("the start page says how an answer is built", (await page.locator(".home-steps li").count()) === 4);
check("the start page lists the four tools", (await page.locator(".home-tools .home-tile").count()) === 4);
check("an amendment that touches the user's work raises a banner on the start page", (await page.locator(".alert-banner").innerText()).includes("1 citas"));
check("html lang is es-MX", (await page.getAttribute("html", "lang")) === "es-MX");
await page.locator(".home-tile", { hasText: "Conversar" }).click();
await page.waitForURL(/\/conversar$/);
await page.locator(".welcome").waitFor();
check("a start-page tile opens its workspace", (await welcome()).includes("consultar") && (await page.locator("h1").innerText()) === "Conversar");
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
check("the top bar links the three workspaces", (await nav.getByRole("link").allInnerTexts()).join("|") === "Conversar|Investigación|Argumentar|Leyes|Verificar|Asuntos");
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
for (let i = 0; i < 10; i++) {
  await page.keyboard.press("Tab");
  tabStops.push(await page.evaluate(() => document.activeElement?.textContent?.trim()));
}
check("the settings button is reachable by keyboard from the top bar", tabStops.includes("Ajustes"), tabStops.slice(0, 9).join(" | "));
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
await page.goto(`${BASE}/conversar`);

// ---- phone width: the top bar stays small, settings behind one button (Brand.md 8.1) ----------
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${BASE}/conversar`);
await page.locator(".welcome").waitFor();
const barHeight = await page.locator(".app-header").evaluate((e) => e.getBoundingClientRect().height);
check("on a phone the top bar is compact", barHeight < 170, `${Math.round(barHeight)}px`);
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
for (const [p, ready] of [["leyes?ley=LFT&p=d.tsegundo.civ.art47", "#law-node-title"], ["verificar", ".verify-form"], ["asuntos?m=0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", "#matter-title"], ["reformas", ".alert-list"]]) {
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
