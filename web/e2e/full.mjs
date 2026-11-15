// Full local flow in a real browser: real API, real Postgres, real local models (Ollama).
//   terminal 1: uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
//   terminal 2: cd web && npm run build && npx next start -H 127.0.0.1 -p 3100   (or scripts/dev-up.ps1)
//   terminal 3: cd web && npm run e2e:full          (credentials from ../.demo-credentials)
// A model answer takes minutes on this hardware: this is not a CI test.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3100";
const creds = Object.fromEntries(
  readFileSync(new URL("../../.demo-credentials", import.meta.url), "utf-8")
    .split("\n").filter((l) => l.includes(":")).map((l) => [l.split(":")[0].trim(), l.slice(l.indexOf(":") + 1).trim()]),
);
const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf-8");
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const consoleErrors = [];
const external = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));
page.on("request", (r) => {
  const u = new URL(r.url());
  if (!["localhost", "127.0.0.1"].includes(u.hostname) && u.protocol.startsWith("http")) external.push(r.url());
});

// A <select> inside a <label> takes the selected option text into its accessible name, so match the
// visible label text exactly instead.
const sel = (name) => page.locator("label.field").filter({ has: page.locator("span", { hasText: new RegExp(`^${name}$`) }) }).locator("select");

async function ask(query) {
  await page.getByLabel("Mensaje").fill(query);
  const before = await page.locator(".turn-assistant").count();
  const t0 = Date.now();
  await page.getByRole("button", { name: "Enviar" }).click();
  await page.locator(".turn-assistant").nth(before).or(page.locator(".form-status")).first().waitFor({ timeout: 600_000 });
  return Math.round((Date.now() - t0) / 1000);
}
async function axe(label) {
  await page.addScriptTag({ content: axeSource });
  const r = await page.evaluate(async () => await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa"] }));
  check(`axe: no violations on ${label}`, r.violations.length === 0, r.violations.map((v) => `${v.id}(${v.nodes.length})`).join(", "));
}

// ---- login ---------------------------------------------------------------------------------
await page.goto(`${BASE}/login`);
await page.getByLabel("Despacho").fill(creds.firm);
await page.getByLabel("Correo electrónico").fill(creds.email);
await page.getByLabel("Contraseña", { exact: true }).fill("incorrecta-12345");
await page.getByRole("button", { name: "Entrar" }).click();
await page.locator(".form-status").waitFor();
check("a wrong password shows the generic message", (await page.locator(".form-status").innerText()).includes("Credenciales no válidas"));
await page.getByLabel("Contraseña", { exact: true }).fill(creds.password);
await page.getByRole("button", { name: "Entrar" }).click();
await page.waitForURL(`${BASE}/`);
await page.locator(".mesa").waitFor();
check("login lands on the start page (one box, three modes)", (await page.locator(".mesa-mode").count()) === 3);
await page.getByRole("navigation", { name: "Secciones" }).getByRole("link", { name: "Conversar" }).click();
await page.getByRole("heading", { name: "Conversar", level: 1 }).waitFor();
await page.getByRole("button", { name: "Cambiar" }).click(); // the scope selectors are folded by default
const cookies = await page.context().cookies();
check("the session cookie is httpOnly and strict", cookies.some((c) => c.name === "session" && c.httpOnly && c.sameSite === "Strict"));
check("the token is not readable from page scripts", !(await page.evaluate(() => document.cookie)).includes("session"));

// ---- selectors come from the closed catalog -------------------------------------------------
await sel("Estado").locator("option").nth(1).waitFor({ state: "attached" }); // the catalog arrives after the page
const states = await sel("Estado").locator("option").allInnerTexts();
check("the state selector lists the catalog (federal-only + 32 MX states)", states.length === 33, `${states.length} options`);
check("municipality is disabled until a state is chosen", await sel("Municipio").isDisabled());
await sel("Estado").selectOption("NL");
const munis = await sel("Municipio").locator("option").allInnerTexts();
check("only the municipalities of Nuevo León are offered", munis.includes("Monterrey") && !munis.includes("Guadalajara"), munis.join(" | "));
await sel("Municipio").selectOption("Monterrey");
check("the stacked scope is shown", (await page.locator(".scope").innerText()).includes("Nuevo León"));
await sel("Estado").selectOption("JAL");
check("changing the state resets the municipality", (await sel("Municipio").inputValue()) === "");
await sel("Estado").selectOption("");
check("federal-only disables the municipality again", await sel("Municipio").isDisabled());

// ---- a real answer with the local models (ADR-016: stored as a conversation) --------------------
await sel("Perfil").selectOption("labor");
const secs = await ask("¿Cuáles son las causales de rescisión de la relación de trabajo sin responsabilidad para el patrón?");
console.log(`      (answered in ${secs}s)`);
const turn = page.locator(".turn-assistant").last();
const answered = (await turn.locator(".answer").count()) > 0;
const refused = (await turn.locator(".refusal").count()) > 0;
check("the query ends in an answer or a refusal, never an error", answered || refused, answered ? "answered" : refused ? "refused" : "error shown");
check("the conversation keeps its fixed scope as read-only chips", (await page.locator(".scope-summary").innerText()).includes("Laboral"));
if (answered) {
  const chips = turn.locator(".cite-mark");
  check("the answer carries numbered verified citations (Brand.md 11.3)", (await chips.count()) >= 1, `${await chips.count()} marks`);
  const label = (await chips.first().getAttribute("aria-label")) ?? "";
  check("a citation's name is the citation string, not the raw id", /art\.|Art|§/i.test(label) && !/MX-|US-/.test(label), label.slice(0, 70));
  check("the answer says what the system checked", (await turn.locator(".verify-strip").innerText()).includes("comprobada"));
  check("the sources panel lists what the conversation cited, numbered, with an excerpt", (await page.locator(".sources-panel .source-card").count()) >= 1 && (await page.locator(".sources-panel .source-num").first().innerText()) === "1");
  await axe("answer in a conversation");
  await chips.first().click();
  await page.locator("dialog.viewer[open]").waitFor();
  await page.locator(".viewer-body").waitFor();
  check("the source viewer opens with the cited passage highlighted", (await page.locator("mark.cited-passage").count()) === 1);
  check("the viewer shows document, version and vigencia", (await page.locator(".viewer-meta").innerText()).includes("Vigente"));
  await axe("source viewer");
  await page.keyboard.press("Escape");
  check("Escape closes the viewer", (await page.locator("dialog.viewer[open]").count()) === 0);
}
const firstUrl = page.url();
check("the open conversation is in the URL", /\?c=[0-9a-f-]{36}$/.test(firstUrl), firstUrl);
await page.reload();
await page.locator(".turn-assistant").first().waitFor();
check("a reload reopens the stored conversation", (await page.locator(".turn-user").count()) === 1 && (await page.locator(".history-item[aria-current=page]").count()) === 1);

await page.getByRole("button", { name: "Eliminar" }).click(); // leave no trace in the test account
await page.locator(".bar-confirm").getByRole("button", { name: "Eliminar" }).click();
await page.locator(".welcome").waitFor();

// ---- a refusal is a first-class component (new conversation) ----------------------------------
await page.getByRole("button", { name: "Nueva conversación" }).click();
await page.locator(".welcome").waitFor();
const secs2 = await ask("receta de pastel de chocolate con tres huevos");
console.log(`      (refusal in ${secs2}s)`);
const refusal = page.locator(".turn-assistant .refusal");
check("an off-topic question is refused", (await refusal.count()) === 1);
check("the refusal is a status region, not an alert/error", (await refusal.getAttribute("role")) === "status" && (await page.locator(".form-status").count()) === 0);
check("the refusal explains what is missing and what to do", (await refusal.innerText()).includes("Qué falta") && (await refusal.innerText()).includes("Qué puede hacer"));
const border = await refusal.evaluate((el) => getComputedStyle(el).borderTopStyle);
check("the refusal uses the dotted 'unsupported' style", border === "dotted", border);
await axe("refusal");
check("the new conversation is in the history", (await page.locator(".history-item[aria-current=page]").count()) === 1);
await page.getByRole("button", { name: "Eliminar" }).click();
await page.locator(".bar-confirm").getByRole("button", { name: "Eliminar" }).click();
await page.locator(".welcome").waitFor();
check("a conversation can be deleted (inline confirmation)", !page.url().includes("?c="));

// ---- a case: created, shown, renamed and deleted without calling the model ------------------------
await page.getByRole("navigation", { name: "Secciones" }).getByRole("link", { name: "Argumentar" }).click();
await page.locator(".case-form").waitFor();
await page.getByLabel("Nombre del caso").fill("E2E caso");
await page.getByLabel("Parte que representa").selectOption("demandado");
await page.getByLabel("Hechos").fill("Hechos de prueba de la E2E.");
await page.getByRole("button", { name: "Crear caso" }).click();
await page.locator(".case-card").waitFor();
check("a new case shows its card with the side from the catalog", (await page.locator(".case-card summary").innerText()).includes("Parte demandada"));
check("the round composer offers attack and strengthen", (await page.getByRole("button", { name: "Enviar a la contraparte" }).count()) === 1 && (await page.getByRole("button", { name: "Fortalecer mi argumento" }).count()) === 1);
await axe("case");
await page.getByRole("button", { name: "Renombrar" }).click();
await page.getByLabel("Nuevo título").fill("E2E caso renombrado");
await page.getByRole("button", { name: "Guardar" }).click();
await page.locator(".conversation-title", { hasText: "E2E caso renombrado" }).waitFor();
// the history refreshes in its own request: wait for it instead of reading it at once
const renamed = await page.locator(".history-item[aria-current=page]", { hasText: "renombrado" }).waitFor({ timeout: 10_000 }).then(() => true, () => false);
check("a case can be renamed (bar and history)", renamed);
await page.getByRole("button", { name: "Eliminar" }).click();
await page.locator(".bar-confirm").getByRole("button", { name: "Eliminar" }).click();
await page.locator(".case-form").waitFor();
check("a case can be deleted", true);

// ---- the tools over the real API (no model involved): matter, navigator, save, verifier, reforms ----
const nav = page.getByRole("navigation", { name: "Secciones" });
await nav.getByRole("link", { name: "Asuntos" }).click();
await page.getByLabel("Nombre del asunto").fill("E2E asunto");
await page.getByRole("button", { name: "Crear asunto" }).click();
await page.locator("#matter-title", { hasText: "E2E asunto" }).waitFor();
check("a matter is created and opened, owned by its creator", (await page.locator(".matter-head").innerText()).includes("Responsable"));
await page.getByLabel("Nueva nota").fill("Nota de la E2E");
await page.getByRole("button", { name: "Agregar nota" }).click();
await page.locator(".matter-items", { hasText: "Nota de la E2E" }).waitFor();
check("a note is added to the matter", true);

await nav.getByRole("link", { name: "Leyes" }).click();
await page.locator(".toc").first().waitFor();
await page.getByLabel("Ley", { exact: true }).selectOption("LFT");
await page.getByLabel("Ir al artículo").fill("47");
await page.getByRole("button", { name: "Ir", exact: true }).click();
await page.locator("#law-node-title", { hasText: "Artículo 47" }).waitFor();
check("the navigator jumps to an article of the real LFT by number", (await page.locator(".law-text").innerText()).includes("Son causas de rescisión"));
check("each fracción of the article is a citable part", (await page.locator(".citable").count()) >= 10, `${await page.locator(".citable").count()} parts`);
const part = page.locator(".citable").filter({ hasText: "fracc. II" }).first();
await part.getByRole("button", { name: "Guardar en asunto" }).click();
await part.getByLabel("Asunto donde guardar").selectOption({ label: "E2E asunto" });
await part.getByRole("button", { name: "Guardar", exact: true }).click();
await part.getByText("Guardada en el asunto.").waitFor();
check("a citable part is saved to the matter", true);

await nav.getByRole("link", { name: "Verificar" }).click();
await page.getByRole("button", { name: "Cargar un texto de ejemplo" }).click();
await page.getByLabel("Nombre del escrito (para el reporte)").fill("E2E escrito");
await page.getByRole("button", { name: "Verificar", exact: true }).click();
await page.locator(".findings").waitFor({ timeout: 30_000 });
const found = await page.locator(".findings").innerText();
check("the verifier checks the real corpus: current article, verbatim quotation, law not loaded",
  found.includes("Vigente") && found.includes("La cita textual coincide") && found.includes("Ley no cargada"), found.replace(/\s+/g, " ").slice(0, 160));
await page.locator(".report-list li", { hasText: "E2E escrito" }).getByRole("button", { name: "Eliminar" }).click();
await page.locator(".report-list li", { hasText: "E2E escrito" }).waitFor({ state: "detached" });
check("a verification report can be deleted", true);

await page.goto(`${BASE}/reformas`);
await page.locator(".checks tbody tr").first().waitFor({ timeout: 15_000 }).catch(() => {});
check("Reformas shows the last check of every watched source", (await page.locator(".checks tbody tr").count()) >= 4, `${await page.locator(".checks tbody tr").count()} rows`);
await axe("reformas (real)");

await page.goto(`${BASE}/asuntos`);
await page.locator(".matter-card", { hasText: "E2E asunto" }).click();
await page.locator("#matter-title").waitFor();
check("the saved part shows in the matter", (await page.locator(".matter").innerText()).includes("art. 47, fracc. II"));
await axe("matter (real)");
await page.getByRole("button", { name: "Eliminar", exact: true }).first().click();
await page.locator(".bar-confirm").getByRole("button", { name: "Eliminar" }).click();
await page.locator("h2#matters-list").waitFor();
const gone = await page.locator(".matter-card", { hasText: "E2E asunto" }).waitFor({ state: "detached", timeout: 10_000 }).then(() => true, () => false);
check("the matter is deleted (the run leaves nothing behind)", gone);

// ---- logout -----------------------------------------------------------------------------------
await page.getByRole("button", { name: "Ajustes" }).click();
await page.locator("dialog.settings-dialog").getByRole("button", { name: "Cerrar sesión" }).click();
await page.waitForURL(`${BASE}/login`);
await page.goto(`${BASE}/conversar`);
check("after logout, the workspaces redirect to /login", new URL(page.url()).pathname === "/login");

check("no request left localhost", external.length === 0, external.join(", "));
const realErrors = consoleErrors.filter((e) => !/Failed to load resource/.test(e));
check("no unexpected console errors", realErrors.length === 0, realErrors.join(" | ").slice(0, 300));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
