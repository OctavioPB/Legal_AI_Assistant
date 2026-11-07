// WS6.T08: the UI guards that run in CI. Every guard also proves it can fail (bad samples).
import { describe, expect, it } from "vitest";
import { join } from "node:path";
import {
  WEB_ROOT,
  brandTokens,
  catalogProblems,
  contrast,
  cssTokens,
  externalUrls,
  flatten,
  literalProblems,
  rawHtmlSinks,
  read,
  rel,
  walk,
} from "./guards";
import esMX from "../src/i18n/messages/es-MX.json";
import enUS from "../src/i18n/messages/en-US.json";
import { decodeClaims, isExpired } from "../src/lib/auth";

const SRC = join(WEB_ROOT, "src");
const tsx = walk(SRC, [".tsx"]);
const sources = walk(SRC, [".ts", ".tsx", ".css"]);

// Keys looked up with a computed name (t(`persona.${code}`), reason keys from the server, ...).
const DYNAMIC_PREFIXES = ["lang.", "theme.", "workspace.persona.", "workspace.workflow.", "workspace.country.", "workspace.stage.", "workspace.error.", "refusal.reason.", "mark.", "login.error."];

describe("catalog parity (es-MX / en-US)", () => {
  it("has the same keys, no empty values and the same placeholders", () => {
    expect(catalogProblems(flatten(esMX), flatten(enUS))).toEqual([]);
  });
  it("fails on a missing key, an empty value and a placeholder mismatch", () => {
    const a = { "x.a": "Hola {name}", "x.b": "dos", "x.c": "" };
    const b = { "x.a": "Hello", "x.d": "four" };
    const problems = catalogProblems(a, b);
    expect(problems).toContain("missing in second catalog: x.b");
    expect(problems).toContain("missing in first catalog: x.d");
    expect(problems).toContain("empty value: x.c");
    expect(problems).toContain("placeholder mismatch: x.a");
  });
  it("uses every key the components ask for", () => {
    const keys = Object.keys(flatten(esMX));
    const used = tsx.map((f) => read(f)).join("\n");
    const unused = keys.filter((k) => {
      const leaf = k.split(".").pop() as string;
      return !used.includes(`"${k}"`) && !used.includes(leaf) && !DYNAMIC_PREFIXES.some((p) => k.startsWith(p));
    });
    expect(unused).toEqual([]);
  });
});

describe("no user-facing literals outside the catalog", () => {
  it("finds none in the components", () => {
    const found = tsx.flatMap((f) => literalProblems(read(f)).map((p) => `${rel(f)}: ${p}`));
    expect(found).toEqual([]);
  });
  it("fails on JSX text, a literal aria-label and an accented string", () => {
    const bad = `export const X = () => <button aria-label="Cerrar">Enviar</button>;
      export const y = "Selección";`;
    const problems = literalProblems(bad);
    expect(problems.join("\n")).toContain('JSX text: "Enviar"');
    expect(problems.join("\n")).toContain('literal aria-label="Cerrar"');
    expect(problems.join("\n")).toContain("accented literal");
  });
  it("accepts catalog calls and non-text attributes", () => {
    const ok = `export const X = () => <a className="wordmark" href="/" aria-label={t("nav.language")}>{t("brand.name")}</a>;`;
    expect(literalProblems(ok)).toEqual([]);
  });
});

describe("no external resources (self-hosted assets only)", () => {
  const publicFiles = walk(join(WEB_ROOT, "public"), [".css", ".html", ".js", ".svg", ".json"]);
  it("references no external URL in sources, styles or public files", () => {
    const found = [...sources, ...publicFiles, join(SRC, "proxy.ts")].flatMap((f) =>
      externalUrls(read(f)).map((u) => `${rel(f)}: ${u}`),
    );
    expect(found).toEqual([]);
  });
  it("detects an external font, a CDN script and a protocol-relative URL", () => {
    expect(externalUrls("@import url(https://fonts.googleapis.com/css2?family=Inter)")).not.toEqual([]);
    expect(externalUrls('<script src="//cdn.example.com/x.js">')).not.toEqual([]);
    expect(externalUrls('xmlns="http://www.w3.org/2000/svg"')).toEqual([]);
  });
  it("ships the font files the stylesheet declares, with licenses", () => {
    const css = read(join(SRC, "app", "globals.css"));
    const files = [...css.matchAll(/url\("\/fonts\/([^"]+)"\)/g)].map((m) => m[1]);
    expect(files.length).toBeGreaterThanOrEqual(6);
    for (const f of files) expect(() => read(join(WEB_ROOT, "public", "fonts", f))).not.toThrow();
    const version = read(join(WEB_ROOT, "public", "fonts", "VERSION.txt"));
    expect(version).toContain("SIL OFL 1.1");
  });
  it("sets a CSP that only allows this origin", async () => {
    const { csp } = await import("../next.config");
    expect(csp).toContain("default-src 'self'");
    expect(csp).not.toMatch(/https?:/);
    expect(csp).toContain("frame-ancestors 'none'");
  });
});

describe("statute text and model output render as text", () => {
  it("has no raw HTML sink in the source", () => {
    const found = sources.flatMap((f) => rawHtmlSinks(read(f)).map((s) => `${rel(f)}: ${s}`));
    expect(found).toEqual([]);
  });
  it("detects the usual sinks", () => {
    expect(rawHtmlSinks("<div dangerouslySetInnerHTML={{__html: x}} />")).not.toEqual([]);
    expect(rawHtmlSinks("el.innerHTML = answer")).not.toEqual([]);
  });
});

describe("Brand.md conformance", () => {
  const brand = brandTokens(read(join(WEB_ROOT, "..", "Brand.md")));
  const css = read(join(SRC, "app", "globals.css"));
  const cssLight = cssTokens(css, ":root {");
  const cssDarkAuto = cssTokens(css, ":root:not([data-theme=\"light\"])");
  const cssDarkManual = cssTokens(css, ":root[data-theme=\"dark\"]");

  it("reads the full token table from Brand.md", () => {
    expect(Object.keys(brand.light).length).toBeGreaterThanOrEqual(16);
  });
  it("matches Brand.md token by token (light)", () => {
    for (const [name, value] of Object.entries(brand.light)) expect(cssLight[name], name).toBe(value);
  });
  it("matches Brand.md token by token (dark: preference and manual)", () => {
    for (const [name, value] of Object.entries(brand.dark)) {
      if (value === brand.light[name]) continue; // unchanged between themes
      expect(cssDarkAuto[name], `auto ${name}`).toBe(value);
      expect(cssDarkManual[name], `manual ${name}`).toBe(value);
    }
  });
  it("meets WCAG AA for every documented text pairing, in both themes", () => {
    for (const [theme, t] of [["light", brand.light], ["dark", brand.dark]] as const) {
      const text: [string, string][] = [
        ["--text", "--bg"], ["--text", "--surface"], ["--text-muted", "--surface"],
        ["--primary", "--bg"], ["--accent-ink", "--bg"], ["--on-primary", "--primary"],
        ["--verified-fg", "--verified-bg"], ["--reasoning-fg", "--reasoning-bg"],
        ["--unsupported-fg", "--unsupported-bg"],
      ]; // prettier-ignore
      for (const [fg, bg] of text) expect(contrast(t[fg], t[bg]), `${theme} ${fg}/${bg}`).toBeGreaterThanOrEqual(4.5);
      for (const [fg, bg] of [["--border", "--bg"], ["--focus", "--bg"]]) {
        expect(contrast(t[fg], t[bg]), `${theme} ${fg}/${bg}`).toBeGreaterThanOrEqual(3);
      }
    }
  });
  it("keeps the pure accent out of small text (it fails AA, hence accent-ink)", () => {
    expect(contrast(brand.light["--accent"], brand.light["--bg"])).toBeLessThan(4.5);
    const textRules = [...css.matchAll(/color:\s*var\(--accent\)/g)];
    expect(textRules).toEqual([]);
  });
  it("places brand overrides last in the stylesheet", () => {
    expect(css.lastIndexOf("Brand overrides")).toBeGreaterThan(css.lastIndexOf(".app-footer"));
  });
  it("detects a drifting token", () => {
    expect(cssTokens(":root { --bg: #ffffff; }", ":root {")["--bg"]).not.toBe(brand.light["--bg"]);
  });
});

describe("route protection", () => {
  it("keeps proxy.ts inside src/ (Next ignores it at the root when the app lives in src/app)", () => {
    expect(() => read(join(SRC, "proxy.ts"))).not.toThrow();
    expect(() => read(join(WEB_ROOT, "proxy.ts"))).toThrow();
  });
});

describe("session helper (UX only; the API enforces authorization)", () => {
  const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const token = (exp?: number) => `${b64({ alg: "HS256" })}.${b64(exp === undefined ? {} : { exp })}.sig`;
  it("treats missing, malformed, expiry-less and expired tokens as expired", () => {
    const now = 1_000_000;
    expect(isExpired(undefined, now)).toBe(true);
    expect(isExpired("garbage", now)).toBe(true);
    expect(isExpired(token(), now)).toBe(true);
    expect(isExpired(token(now - 1), now)).toBe(true);
    expect(isExpired(token(now + 3600), now)).toBe(false);
  });
  it("decodes claims without trusting them", () => {
    expect(decodeClaims(token(5))?.exp).toBe(5);
    expect(decodeClaims("a.b.c")).toBeNull();
  });
});
