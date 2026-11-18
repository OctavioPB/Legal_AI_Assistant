// The lawyer's review page (evals/e2e/review_page.html) is published as an artifact from a template.
// No Python test parses its script, so a syntax error would only show up in the reviewer's browser.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(__dirname, "..", "..", "evals", "e2e", "review_page.html"), "utf8");
const script = (page.match(/<script>([\s\S]*)<\/script>/) ?? [])[1] ?? "";

describe("review page template (E2E packet)", () => {
  it("has one script that parses", () => {
    expect(script.length).toBeGreaterThan(1000);
    // Function() parses without running: a syntax error throws here.
    expect(() => new Function(script)).not.toThrow();
  });
  it("has exactly one data slot and renders without data", () => {
    expect(page.split("/*REVIEW_DATA*/null").length - 1).toBe(1);
    expect(script).toContain("if (!DATA)");
  });
  it("renders model and statute text as text, never as HTML", () => {
    expect(script).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  });
  it("loads nothing but Google Fonts", () => {
    const hosts = [...page.matchAll(/https:\/\/([a-z0-9.-]+)/gi)].map((m) => m[1]);
    expect(new Set(hosts)).toEqual(new Set(["fonts.googleapis.com"]));
  });
  it("defines its colours for light, system-dark and forced-dark", () => {
    expect(page).toContain('@media (prefers-color-scheme: dark) { :root:not([data-theme="light"])');
    expect(page).toContain(':root[data-theme="dark"]');
  });
});
