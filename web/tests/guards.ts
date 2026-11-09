// Detectors behind the WS6.T08 CI guards. Each one has a self-test with a bad sample, so a
// guard that silently stopped detecting would fail too (Library-RAG lesson: eval tooling has bugs).
import { parse } from "@babel/parser";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

export const WEB_ROOT = join(__dirname, "..");

export function walk(dir: string, exts: string[], skip = ["node_modules", ".next", "tests"]): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (skip.includes(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full, exts, skip));
    else if (exts.some((e) => name.endsWith(e))) out.push(full);
  }
  return out;
}

export function rel(file: string): string {
  return relative(WEB_ROOT, file).replaceAll("\\", "/");
}

// ---- catalog parity -----------------------------------------------------------------------
export function flatten(obj: unknown, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  if (typeof obj === "string") return { [prefix]: obj };
  if (obj && typeof obj === "object") {
    for (const [k, v] of Object.entries(obj)) Object.assign(out, flatten(v, prefix ? `${prefix}.${k}` : k));
  }
  return out;
}

export function placeholders(message: string): string[] {
  return [...message.matchAll(/\{(\w+)/g)].map((m) => m[1]).sort();
}

export function catalogProblems(a: Record<string, string>, b: Record<string, string>): string[] {
  const problems: string[] = [];
  for (const key of Object.keys(a)) if (!(key in b)) problems.push(`missing in second catalog: ${key}`);
  for (const key of Object.keys(b)) if (!(key in a)) problems.push(`missing in first catalog: ${key}`);
  for (const [key, value] of Object.entries(a)) {
    if (!value.trim()) problems.push(`empty value: ${key}`);
    if (key in b && placeholders(value).join() !== placeholders(b[key]).join()) {
      problems.push(`placeholder mismatch: ${key}`);
    }
  }
  return problems;
}

// ---- hardcoded user-facing literals --------------------------------------------------------
const TEXT_ATTRS = new Set(["aria-label", "title", "placeholder", "alt", "label", "aria-description"]);
const ACCENTED = /[À-ÿ]/;

interface Node {
  type: string;
  [key: string]: unknown;
}

function visit(node: unknown, fn: (n: Node) => void): void {
  if (Array.isArray(node)) return node.forEach((n) => visit(n, fn));
  if (node && typeof node === "object") {
    const n = node as Node;
    if (typeof n.type === "string") fn(n);
    for (const [k, v] of Object.entries(n)) {
      if (k === "loc" || k === "extra" || k === "leadingComments" || k === "trailingComments") continue;
      visit(v, fn);
    }
  }
}

/** JSX text, user-facing attribute literals and accented string literals in a .tsx source. */
export function literalProblems(source: string): string[] {
  const ast = parse(source, { sourceType: "module", plugins: ["typescript", "jsx"] });
  const problems: string[] = [];
  visit(ast.program, (n) => {
    if (n.type === "JSXText") {
      const text = String(n.value).trim();
      if (/[A-Za-zÀ-ÿ]/.test(text)) problems.push(`JSX text: "${text}"`);
    } else if (n.type === "JSXAttribute") {
      const name = (n.name as { name?: string }).name ?? "";
      const value = n.value as Node | null;
      if (TEXT_ATTRS.has(name) && value?.type === "StringLiteral") {
        problems.push(`literal ${name}="${String(value.value)}"`);
      }
    } else if (n.type === "StringLiteral" && ACCENTED.test(String(n.value))) {
      problems.push(`accented literal: "${String(n.value)}"`);
    }
  });
  return problems;
}

// ---- external resources --------------------------------------------------------------------
const URL_RE = /(?:https?:)?\/\/[a-z0-9][a-z0-9.-]*\.[a-z]{2,}[^\s"'`)]*/gi;
const ALLOWED_HOSTS = new Set(["www.w3.org"]); // XML namespaces are identifiers, not fetches

export function externalUrls(source: string): string[] {
  return [...source.matchAll(URL_RE)]
    .map((m) => m[0])
    .filter((u) => !ALLOWED_HOSTS.has(u.replace(/^(https?:)?\/\//i, "").split(/[/?#]/)[0].toLowerCase()));
}

// ---- unsafe HTML -----------------------------------------------------------------------------
export function rawHtmlSinks(source: string): string[] {
  return [...source.matchAll(/dangerouslySetInnerHTML|\.innerHTML\s*=|insertAdjacentHTML|document\.write\(/g)].map(
    (m) => m[0],
  );
}

// ---- contrast --------------------------------------------------------------------------------
function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}
function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export interface BrandTokens {
  light: Record<string, string>;
  dark: Record<string, string>;
}

/** Reads the token table of Brand.md section 1: `| `--name` | `#light` | `#dark` | use |`. */
export function brandTokens(markdown: string): BrandTokens {
  const light: Record<string, string> = {};
  const dark: Record<string, string> = {};
  for (const line of markdown.split("\n")) {
    if (!line.startsWith("| `--")) continue;
    const cells = line.split("|").map((c) => c.trim());
    const pair = cells[1].match(/--([a-z]+)-fg\/bg/);
    const names = pair ? [`--${pair[1]}-fg`, `--${pair[1]}-bg`] : (cells[1].match(/--[a-z0-9-]+/g) ?? []);
    const lights = cells[2].match(/#[0-9a-fA-F]{6}/g) ?? [];
    const darks = cells[3].match(/#[0-9a-fA-F]{6}/g) ?? [];
    names.forEach((name, i) => {
      if (lights[i]) light[name] = lights[i].toLowerCase();
      if (darks[i]) dark[name] = darks[i].toLowerCase();
    });
  }
  return { light, dark };
}

/** Token values declared in globals.css for a theme block. */
export function cssTokens(css: string, selectorStart: string): Record<string, string> {
  const start = css.indexOf(selectorStart);
  if (start < 0) return {};
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]] = m[2].toLowerCase();
  return out;
}

export function read(path: string): string {
  return readFileSync(path, "utf-8");
}
