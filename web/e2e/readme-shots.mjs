// Screenshots for the README (not a test). Signs in with the demo account (../.demo-credentials, never
// committed) against the running local app and saves PNGs of the main screens, from the sample data of
// the fictional E2E-LAB-01 case (docs/EVALUACION-E2E.md).
//   node e2e/readme-shots.mjs <out-dir>        (BASE_URL defaults to http://127.0.0.1:3100)
// Needs the API (:8000) and the built web app (:3100) running; takes no model call.
import { readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3100";
const OUT = process.argv[2] ?? "shots";
mkdirSync(OUT, { recursive: true });
const creds = Object.fromEntries(
  readFileSync(new URL("../../.demo-credentials", import.meta.url), "utf-8").split(/\r?\n/).filter((l) => l.includes(":")).map((l) => [l.split(":")[0].trim(), l.slice(l.indexOf(":") + 1).trim()]),
);

const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--lang=es-MX"] });
// es-MX so the browser's own controls (the file picker) speak Spanish like the page.
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1150 }, deviceScaleFactor: 1, locale: "es-MX" });
const page = await ctx.newPage();
const shot = async (name, opts = {}) => {
  await page.waitForTimeout(opts.wait ?? 1200);
  await page.evaluate(() => window.scrollTo(0, 0));
  if (opts.height) {
    await page.setViewportSize({ width: 1440, height: opts.height });
    await page.waitForTimeout(600);
  } else if (opts.tall) {
    // a viewport as tall as the page, so the side rail fills its column instead of ending mid-image
    const h = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width: 1440, height: Math.min(Math.max(h, 900), opts.max ?? 2400) });
    await page.waitForTimeout(400);
  }
  await page.screenshot({ path: join(OUT, `${name}.png`) });
  if (opts.tall || opts.height) await page.setViewportSize({ width: 1440, height: 1150 });
  console.log("saved", name);
};
const openFromHistory = async (path, title, ready = ".turn-assistant") => {
  await page.goto(`${BASE}${path}`);
  await page.getByText(title, { exact: false }).first().click();
  await page.locator(ready).first().waitFor();
};

await ctx.addCookies([{ name: "theme", value: "light", url: BASE }]);
await page.goto(`${BASE}/login`);
await page.getByLabel("Despacho").fill(creds.firm);
await page.getByLabel("Correo electrónico").fill(creds.email);
await page.getByLabel("Contraseña", { exact: true }).fill(creds.password);
await page.getByRole("button", { name: "Entrar" }).click();
await page.waitForURL(`${BASE}/`);

// ---- start page ---------------------------------------------------------------------------
await page.goto(`${BASE}/`);
await page.locator(".recent-card").first().waitFor();
await shot("home");

// ---- Conversar: an answer with its numbered citations, then the source opened on the cited passage ---
await openFromHistory("/conversar", "Consultas del caso");
await page.locator(".cite-mark").first().waitFor();
await shot("conversar");
await page.locator(".cite-mark").first().click();
await page.locator(".cited-passage").first().waitFor();
await shot("source-viewer");

// ---- Investigación and Argumentar --------------------------------------------------------------
await openFromHistory("/investigacion", "Prestaciones", ".dossier");
await shot("investigacion");
await openFromHistory("/argumentar", "Martínez Solís", ".round-answer");
// a taller window, so the case, the argument and the opposing counsel's round fit above the composer
await shot("argumentar", { height: 2000, wait: 800 });

// ---- Leyes: the structure of a law and one article ---------------------------------------------
await page.goto(`${BASE}/leyes?ley=LFT&p=d.tsegundo.civ.art47`);
await page.locator("h2, h3").filter({ hasText: "Artículo 47" }).first().waitFor();
await shot("leyes", { wait: 1500 });

// ---- Verificar: the stored report of the matter's complaint (references and statuses, never the text) ----
await page.goto(`${BASE}/verificar`);
await page.locator("li", { hasText: "Escrito inicial de demanda" }).getByRole("button", { name: "Ver" }).click();
await page.locator(".findings").waitFor();
await shot("verificar", { tall: true, max: 3400 });

// ---- Asuntos: a matter with its work, its key-points sheet, notes and members ------------------
await page.goto(`${BASE}/asuntos`);
await page.locator(".matter-card").first().click();
await page.locator(".matter-head").waitFor();
await shot("asuntos", { tall: true });
const sheet = page.locator("a[href*='/asuntos/hoja']").first();
if (await sheet.count()) {
  await sheet.click();
  await page.waitForURL(/hoja/);
  await page.locator("main").waitFor();
  await shot("hoja", { tall: true, wait: 2000 });
} else console.log("no answered sheet in this matter: hoja not saved");

// ---- the rest of the tools ---------------------------------------------------------------------
for (const [name, path, tall] of [
  ["documentos", "/documentos", true],
  ["noticias", "/noticias", false],
  ["reformas", "/reformas", false],
]) {
  await page.goto(`${BASE}${path}`);
  await shot(name, { wait: 2000, tall });
}

// ---- dark theme and a phone --------------------------------------------------------------------
await ctx.addCookies([{ name: "theme", value: "dark", url: BASE }]);
await openFromHistory("/conversar", "Consultas del caso");
await page.locator(".cite-mark").first().waitFor();
await shot("conversar-dark");
await ctx.addCookies([{ name: "theme", value: "light", url: BASE }]);
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${BASE}/`);
await shot("phone-home");

await browser.close();
