import { describe, expect, it } from "vitest";
import { citedIds, parseAnswer, parseInline } from "../src/lib/answer";
import { countries, municipalitiesOf, resolve, scopeChain, select, statesOf, type Jurisdiction, type Selection } from "../src/lib/jurisdiction";
import { isSameOriginJson, match } from "../src/lib/proxy";

const J = (id: number, country: "MX" | "US", level: Jurisdiction["level"], state: string | null, muni: string | null, es: string, en = es): Jurisdiction => ({
  id, country, level, state_code: state, municipality: muni, name_es: es, name_en: en,
});
const ROWS: Jurisdiction[] = [
  J(1, "MX", "federal", null, null, "Federal (México)", "Federal (Mexico)"),
  J(2, "MX", "state", "NL", null, "Nuevo León"),
  J(3, "MX", "state", "JAL", null, "Jalisco"),
  J(4, "MX", "municipal", "NL", "Monterrey", "Monterrey, Nuevo León"),
  J(5, "MX", "municipal", "NL", "San Pedro Garza García", "San Pedro Garza García, Nuevo León"),
  J(6, "MX", "municipal", "JAL", "Guadalajara", "Guadalajara, Jalisco"),
  J(7, "US", "federal", null, null, "Federal (Estados Unidos)", "Federal (United States)"),
  J(8, "US", "state", "CA", null, "California"),
];
const MX: Selection = { country: "MX", state: null, municipality: null };

describe("answer parsing", () => {
  it("extracts grouped, spaced and cased citation markers in order", () => {
    const [a] = parseInline("Procede [cite: A-1; B-2] y también [ CITE : C-3 , A-1 ].");
    expect(a).toEqual({ kind: "text", text: "Procede " });
    const ids = parseAnswer("x [cite: A-1; B-2] y [ CITE : C-3 , A-1 ]").flatMap((b) => ("inline" in b ? b.inline : [])).filter((i) => i.kind === "cite");
    expect(ids).toEqual([{ kind: "cite", ids: ["A-1", "B-2"] }, { kind: "cite", ids: ["C-3", "A-1"] }]);
    expect(citedIds(parseAnswer("x [cite: A-1; B-2] y [cite: C-3, A-1]"))).toEqual(["A-1", "B-2", "C-3"]);
  });

  it("recognizes headings, list items and paragraphs and the two reasoning marks", () => {
    const blocks = parseAnswer("## (I) Planteamiento\n- punto uno [cite: X-1]\n1. punto dos\nTexto libre [razonamiento estratégico]\nOtro [análisis propio]");
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "item", "item", "paragraph", "paragraph"]);
    expect(blocks.map((b) => ("marked" in b ? b.marked : "-"))).toEqual(["-", null, null, "strategic", "own"]);
  });

  it("never produces HTML: markup in the text stays literal text", () => {
    const [blk] = parseAnswer('<img src=x onerror=alert(1)> **negrita** [cite: A]');
    const b = blk as Extract<typeof blk, { inline: unknown }>;
    expect(b.inline.map((i) => i.kind)).toEqual(["text", "bold", "text", "cite"]);
    expect(b.inline[0]).toEqual({ kind: "text", text: "<img src=x onerror=alert(1)> " });
  });

  it("is linear on hostile input", () => {
    const t0 = performance.now();
    parseAnswer("[cite: ".repeat(50_000));
    parseAnswer("**".repeat(50_000));
    parseAnswer("[".repeat(100_000));
    expect(performance.now() - t0).toBeLessThan(2000);
  });
});

describe("jurisdiction selection", () => {
  it("resolves municipal, then state, then federal", () => {
    expect(resolve(ROWS, { country: "MX", state: "NL", municipality: "Monterrey" })?.id).toBe(4);
    expect(resolve(ROWS, { country: "MX", state: "NL", municipality: null })?.id).toBe(2);
    expect(resolve(ROWS, MX)?.id).toBe(1);
    expect(resolve(ROWS, { country: "US", state: "CA", municipality: null })?.id).toBe(8);
  });

  it("offers only the children of the chosen parent (never a sibling)", () => {
    expect(municipalitiesOf(ROWS, "MX", "NL").map((m) => m.municipality)).toEqual(["Monterrey", "San Pedro Garza García"]);
    expect(municipalitiesOf(ROWS, "MX", null)).toEqual([]);
    expect(statesOf(ROWS, "US").map((s) => s.state_code)).toEqual(["CA"]);
    expect(countries(ROWS)).toEqual(["MX", "US"]);
  });

  it("resets the lower levels when a parent changes", () => {
    const start: Selection = { country: "MX", state: "NL", municipality: "Monterrey" };
    expect(select(ROWS, start, { state: "JAL" })).toEqual({ country: "MX", state: "JAL", municipality: null });
    expect(select(ROWS, start, { country: "US" })).toEqual({ country: "US", state: null, municipality: null });
    expect(select(ROWS, start, { municipality: "San Pedro Garza García" }).municipality).toBe("San Pedro Garza García");
  });

  it("refuses to express a scope that is not in the catalog", () => {
    expect(select(ROWS, MX, { state: "ZZ" })).toEqual({ country: "MX", state: null, municipality: null });
    expect(select(ROWS, { country: "MX", state: "NL", municipality: null }, { municipality: "Guadalajara" }).municipality).toBeNull();
    expect(resolve(ROWS, { country: "MX", state: "JAL", municipality: "Monterrey" })).toBeNull();
  });

  it("shows the stacked scope, federal always included", () => {
    expect(scopeChain(ROWS, { country: "MX", state: "NL", municipality: "Monterrey" }, "es-MX")).toEqual(["Federal (México)", "Nuevo León", "Monterrey"]);
    expect(scopeChain(ROWS, { country: "US", state: "CA", municipality: null }, "en-US")).toEqual(["Federal (United States)", "California"]);
  });
});

describe("gateway allow-list", () => {
  it("forwards only the exact routes with the exact method", () => {
    expect(match("GET", ["me"])).not.toBeNull();
    expect(match("GET", ["catalog", "jurisdictions"])).not.toBeNull();
    expect(match("POST", ["conversations", "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b", "messages", "stream"])?.stream).toBe(true);
    expect(match("GET", ["sources", "MX-FED-LFT-ART47-FRACX-v2025.06"])).not.toBeNull();
  });

  it("rejects everything else, including traversal, encoded tricks and wrong methods", () => {
    for (const [m, p] of [
      ["GET", ["traces", "abc"]], ["GET", ["health"]], ["POST", ["auth", "login"]], ["GET", ["chat"]], ["POST", ["chat"]], ["POST", ["chat", "stream"]], ["POST", ["me"]],
      ["GET", ["sources", ".."]], ["GET", ["sources", "a/../traces"]], ["GET", ["sources", "%2e%2e"]], ["GET", ["sources", "a\\b"]],
      ["GET", ["catalog", "jurisdictions", "extra"]], ["GET", ["", "me"]], ["DELETE", ["me"]],
    ] as [string, string[]][]) {
      expect(match(m, p), `${m} ${p.join("/")}`).toBeNull();
    }
  });
});

describe("CSRF guard on state-changing route handlers", () => {
  const HOST = { host: "127.0.0.1:3100" };
  const h = (o: Record<string, string>) => new Headers({ ...HOST, ...o });
  it("accepts the app's own JSON fetches (with or without Origin, any host name the user typed)", () => {
    expect(isSameOriginJson(h({ "content-type": "application/json" }))).toBe(true);
    expect(isSameOriginJson(h({ "content-type": "application/json; charset=utf-8", origin: "http://127.0.0.1:3100", "sec-fetch-site": "same-origin" }))).toBe(true);
    expect(isSameOriginJson(new Headers({ host: "localhost:3100", "content-type": "application/json", origin: "http://localhost:3100" }))).toBe(true);
  });
  it("rejects a cross-site text/plain form smuggling a JSON body (login CSRF)", () => {
    expect(isSameOriginJson(h({ "content-type": "text/plain", origin: "https://evil.example", "sec-fetch-site": "cross-site" }))).toBe(false);
    expect(isSameOriginJson(h({ "content-type": "text/plain" }))).toBe(false);
    expect(isSameOriginJson(h({}))).toBe(false);
  });
  it("rejects JSON marked cross-site or from another origin", () => {
    expect(isSameOriginJson(h({ "content-type": "application/json", "sec-fetch-site": "cross-site" }))).toBe(false);
    expect(isSameOriginJson(h({ "content-type": "application/json", "sec-fetch-site": "same-site" }))).toBe(false);
    expect(isSameOriginJson(h({ "content-type": "application/json", origin: "http://127.0.0.1:3101" }))).toBe(false);
    expect(isSameOriginJson(h({ "content-type": "application/json", origin: "null" }))).toBe(false);
  });
});
