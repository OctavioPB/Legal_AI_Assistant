// Real-browser checks (system Edge via playwright-core; no browser download).
//   npm run build && npx next start -p 3100   (in another terminal)  then:  node e2e/browser.mjs
// Verifies what unit tests cannot: language switching WITHOUT a reload, theme, keyboard,
// focus visibility, CSP violations, external requests and axe accessibility rules.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const BASE = process.env.BASE_URL ?? "http://localhost:3100";
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
check("home renders in es-MX by default", (await page.locator("h1").innerText()).includes("jurisdicción"));
check("html lang is es-MX", (await page.getAttribute("html", "lang")) === "es-MX");

// ---- language toggle without reload ---------------------------------------------------------
await page.evaluate(() => {
  window.__marker = "same-document";
});
let navigations = 0;
page.on("framenavigated", () => navigations++);
await page.getByRole("button", { name: "English" }).click();
await page.waitForFunction(() => document.documentElement.lang === "en-US");
check("language switches to en-US in place", (await page.locator("h1").innerText()).includes("Start by selecting"));
check("no reload happened (document marker and navigation count)", (await page.evaluate(() => window.__marker)) === "same-document" && navigations === 0);
check("footer disclaimer follows the language", (await page.locator("footer").innerText()).includes("Research tool"));
check("aria-pressed reflects the active language", (await page.getByRole("button", { name: "English" }).getAttribute("aria-pressed")) === "true");
await page.reload();
check("locale persists across reload (cookie)", (await page.getAttribute("html", "lang")) === "en-US" && (await page.locator("h1").innerText()).includes("Start by selecting"));
await page.getByRole("button", { name: "Español" }).click();
await page.waitForFunction(() => document.documentElement.lang === "es-MX");

// ---- theme ------------------------------------------------------------------------------------
const bgOf = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const light = await bgOf();
await page.getByRole("button", { name: "Oscuro" }).click();
await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
const dark = await bgOf();
check("dark theme changes the page background to the Brand.md token", light === "rgb(250, 250, 248)" && dark === "rgb(15, 19, 24)", `${light} -> ${dark}`);
await page.reload();
check("theme persists across reload without a flash (SSR from cookie)", (await page.getAttribute("html", "data-theme")) === "dark");
await page.getByRole("button", { name: "Claro" }).click();

// ---- fonts ------------------------------------------------------------------------------------
const fonts = await page.evaluate(async () => {
  await document.fonts.ready;
  return [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replaceAll('"', ""));
});
check("self-hosted fonts actually load", fonts.includes("Inter"), fonts.join(", "));

// ---- keyboard and focus -----------------------------------------------------------------------
await page.goto(`${BASE}/`);
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
check("all header controls are reachable by keyboard", ["Español", "English", "Sistema", "Claro", "Oscuro"].every((n) => tabStops.includes(n)), tabStops.slice(0, 6).join(" | "));
await page.getByRole("button", { name: "English" }).focus();
await page.keyboard.press("Enter");
check("a toggle can be activated with the keyboard", (await page.getAttribute("html", "lang")) === "en-US");
await page.getByRole("button", { name: "Español" }).click();

// ---- target size (WCAG 2.5.5 as per Brand.md section 6: >= 44px) ---------------------------
const small = await page.evaluate(() => [...document.querySelectorAll("button, a.wordmark, input")].map((e) => [e.textContent?.trim() || e.name, e.getBoundingClientRect()]).filter(([, r]) => r.height < 44 && r.width > 0).map(([n, r]) => `${n}:${Math.round(r.height)}px`));
check("interactive controls are at least 44px tall", small.length === 0, small.join(", "));

// ---- axe, both themes and both pages ---------------------------------------------------------
async function axe(label) {
  await page.addScriptTag({ content: axeSource });
  const r = await page.evaluate(async () => await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"] }));
  check(`axe: no violations on ${label}`, r.violations.length === 0, r.violations.map((v) => `${v.id}(${v.nodes.length})`).join(", "));
}
await page.goto(`${BASE}/`);
await axe("home (light)");
await page.getByRole("button", { name: "Oscuro" }).click();
await axe("home (dark)");
await page.getByRole("button", { name: "Claro" }).click();
await page.goto(`${BASE}/login`);
await axe("login (light)");
await page.getByRole("button", { name: "Oscuro" }).click();
await axe("login (dark)");
await page.getByRole("button", { name: "Sistema" }).click();

// ---- login form behaviour (API is down on purpose) --------------------------------------------
await page.goto(`${BASE}/login`);
await page.getByLabel("Correo electrónico").fill("abogada@despacho.mx");
await page.getByLabel("Contraseña").fill("secreto");
await page.getByRole("button", { name: "Entrar" }).click();
// Next injects its own empty role=alert route announcer: scope to the form message.
await page.locator(".form-status").waitFor();
check("API down shows an unavailable message, not a crash", (await page.locator(".form-status").innerText()).includes("no está disponible"));
check("the message is announced to assistive tech (role=alert)", (await page.locator(".form-status").getAttribute("role")) === "alert");
check("the submit button recovers after the error", await page.getByRole("button", { name: "Entrar" }).isEnabled());
check("the password is never put in the URL or the DOM text", !page.url().includes("secreto") && !(await page.content()).includes("secreto"));

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
