// Full local flow in a real browser: real API, real Postgres, real local models (Ollama).
//   terminal 1: uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
//   terminal 2: cd web && npm run build && npx next start -p 3100
//   terminal 3: cd web && npm run e2e:full          (credentials from ../.demo-credentials)
// A model answer takes minutes on this hardware: this is not a CI test.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const BASE = process.env.BASE_URL ?? "http://localhost:3100";
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

async function ask(query, { persona, workflow } = {}) {
  if (persona) await page.getByLabel("Perfil", { exact: false }).first().selectOption(persona);
  if (workflow) await page.getByRole("button", { name: workflow }).click();
  await page.getByLabel("Consulta").fill(query);
  const t0 = Date.now();
  await page.getByRole("button", { name: "Consultar" }).click();
  await page.locator(".answer, .refusal, .form-status").first().waitFor({ timeout: 600_000 });
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
await page.getByLabel("Contraseña").fill("incorrecta-12345");
await page.getByRole("button", { name: "Entrar" }).click();
await page.locator(".form-status").waitFor();
check("a wrong password shows the generic message", (await page.locator(".form-status").innerText()).includes("Credenciales no válidas"));
await page.getByLabel("Contraseña").fill(creds.password);
await page.getByRole("button", { name: "Entrar" }).click();
await page.waitForURL(`${BASE}/`);
await page.getByRole("heading", { name: "Consulta jurídica" }).waitFor();
check("login lands on the workspace", true);
const cookies = await page.context().cookies();
check("the session cookie is httpOnly and strict", cookies.some((c) => c.name === "session" && c.httpOnly && c.sameSite === "Strict"));
check("the token is not readable from page scripts", !(await page.evaluate(() => document.cookie)).includes("session"));

// ---- selectors come from the closed catalog -------------------------------------------------
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

// ---- a real answer with the local models -----------------------------------------------------
const secs = await ask("¿Cuáles son las causales de rescisión de la relación de trabajo sin responsabilidad para el patrón?", { persona: "labor" });
console.log(`      (answered in ${secs}s)`);
const answered = (await page.locator(".answer").count()) > 0;
const refused = (await page.locator(".refusal").count()) > 0;
check("the query ends in an answer or a refusal, never an error", answered || refused, answered ? "answered" : refused ? "refused" : "error shown");
if (answered) {
  const chips = page.locator(".answer .chip");
  check("the answer carries verified citation chips", (await chips.count()) >= 1, `${await chips.count()} chips`);
  const label = await chips.first().innerText();
  check("a chip shows the citation string, not the raw id", /art\.|Art|§/i.test(label) && !/^MX-|^US-/.test(label.trim()), label.trim().slice(0, 70));
  await axe("answer view");
  await chips.first().click();
  await page.locator("dialog.viewer[open]").waitFor();
  await page.locator(".viewer-body").waitFor();
  check("the source viewer opens with the cited passage highlighted", (await page.locator("mark.cited-passage").count()) === 1);
  check("the viewer shows document, version and vigencia", (await page.locator(".viewer-meta").innerText()).includes("Vigente"));
  await axe("source viewer");
  await page.keyboard.press("Escape");
  check("Escape closes the viewer", (await page.locator("dialog.viewer[open]").count()) === 0);
  // language switch keeps the answer and translates the interface
  await page.getByRole("button", { name: "English" }).click();
  check("switching language keeps the answer and translates the labels", (await page.locator(".answer h2").innerText()) === "Answer" && (await page.locator(".answer").count()) === 1);
  await page.getByRole("button", { name: "Español" }).click();
}

// ---- a refusal is a first-class component ----------------------------------------------------
const secs2 = await ask("receta de pastel de chocolate con tres huevos");
console.log(`      (refusal in ${secs2}s)`);
check("an off-topic question is refused", (await page.locator(".refusal").count()) === 1);
check("the refusal is a status region, not an alert/error", (await page.locator(".refusal").getAttribute("role")) === "status" && (await page.locator(".form-status").count()) === 0);
check("the refusal explains what is missing and what to do", (await page.locator(".refusal").innerText()).includes("Qué falta") && (await page.locator(".refusal").innerText()).includes("Qué puede hacer"));
const border = await page.locator(".refusal").evaluate((el) => getComputedStyle(el).borderTopStyle);
check("the refusal uses the dotted 'unsupported' style", border === "dotted", border);
await axe("refusal");

// ---- logout -----------------------------------------------------------------------------------
await page.getByRole("button", { name: "Cerrar sesión" }).click();
await page.waitForURL(`${BASE}/login`);
await page.goto(`${BASE}/`);
check("after logout, / redirects to /login", new URL(page.url()).pathname === "/login");

check("no request left localhost", external.length === 0, external.join(", "));
const realErrors = consoleErrors.filter((e) => !/Failed to load resource/.test(e));
check("no unexpected console errors", realErrors.length === 0, realErrors.join(" | ").slice(0, 300));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
