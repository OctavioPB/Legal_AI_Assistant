// Visual review helper (not a test): logs in with the demo account and saves screenshots of every page,
// light and dark, desktop and phone. Usage: node e2e/shots.mjs <out-dir>   (BASE_URL defaults to :3100)
import { readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3100";
const OUT = process.argv[2] ?? "shots";
mkdirSync(OUT, { recursive: true });
const creds = Object.fromEntries(
  readFileSync(join(process.cwd(), "..", ".demo-credentials"), "utf-8").split(/\r?\n/).filter((l) => l.includes(":")).map((l) => [l.split(":")[0].trim(), l.slice(l.indexOf(":") + 1).trim()]),
);
const browser = await chromium.launch({ channel: "msedge", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await page.goto(`${BASE}/login`);
await page.screenshot({ path: join(OUT, "00-login.png") });
await page.getByLabel("Despacho").fill(creds.firm);
await page.getByLabel("Correo electrónico").fill(creds.email);
await page.getByLabel("Contraseña", { exact: true }).fill(creds.password);
await page.getByRole("button", { name: "Entrar" }).click();
await page.waitForURL(`${BASE}/`);
const pages = (process.env.PAGES ?? "/,/conversar,/investigacion,/argumentar,/leyes?ley=LFT&p=d.tsegundo.civ.art47,/verificar,/asuntos,/reformas").split(",");
for (const theme of ["light", "dark"]) {
  await ctx.addCookies([{ name: "theme", value: theme, url: BASE }]);
  for (const [i, p] of pages.entries()) {
    await page.goto(`${BASE}${p}`);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: join(OUT, `${theme}-${String(i + 1).padStart(2, "0")}-${p.split("?")[0].replace(/\W/g, "") || "inicio"}.png`) });
  }
}
await page.setViewportSize({ width: 390, height: 844 });
await ctx.addCookies([{ name: "theme", value: "light", url: BASE }]);
for (const p of ["/", "/conversar"]) {
  await page.goto(`${BASE}${p}`);
  await page.waitForTimeout(1200);
  await page.screenshot({ path: join(OUT, `phone-${p.replace(/\W/g, "") || "inicio"}.png`), fullPage: true });
}
await browser.close();
console.log("saved to", OUT);
