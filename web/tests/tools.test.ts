import { describe, expect, it } from "vitest";
import { diffWords } from "../src/lib/diff";
import { forwardedQuery, isSameOriginJson, match } from "../src/lib/proxy";

const id = "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b";

describe("gateway: navigator, matters, verifier, reforms", () => {
  it("forwards exactly the new routes, each with its methods", () => {
    for (const p of [["laws"], ["laws", "LFT", "toc"], ["laws", "LFT", "find"], ["laws", "LFT", "node"], ["laws", "LFT", "history"]]) {
      expect(match("GET", p)).not.toBeNull();
      expect(match("POST", p)).toBeNull();
    }
    expect(match("GET", ["laws", "LFT", "raw"])).toBeNull();
    expect(match("GET", ["laws", "../etc", "toc"])).toBeNull();
    expect(match("GET", ["matters"])).not.toBeNull();
    expect(match("GET", ["matters", "colleagues"])).not.toBeNull();
    for (const m of ["GET", "PATCH", "DELETE"]) expect(match(m, ["matters", id])).not.toBeNull();
    for (const sub of ["members", "notes", "sources"]) {
      expect(match("POST", ["matters", id, sub])).not.toBeNull();
      expect(match("DELETE", ["matters", id, sub, id])).not.toBeNull();
    }
    expect(match("DELETE", ["matters", id, "members", "everyone"])).toBeNull();
    expect(match("POST", ["verify"])?.body ?? "json").toBe("json");
    expect(match("POST", ["verify", "pdf"])?.body).toBe("pdf");
    expect(match("GET", ["verify", "reports", id])).not.toBeNull();
    expect(match("GET", ["reforms", "alerts"])).not.toBeNull();
    expect(match("GET", ["reforms", id])).not.toBeNull();
    expect(match("POST", ["reforms"])).toBeNull();
  });

  it("forwards only the parameters each route declares, validated", () => {
    const node = match("GET", ["laws", "LFT", "node"])!;
    expect(forwardedQuery("?path=d.tsegundo.civ.art47&x=1", node)).toBe("?path=d.tsegundo.civ.art47");
    expect(forwardedQuery("?path=a;drop", node)).toBe("");
    const find = match("GET", ["laws", "LFT", "find"])!;
    expect(forwardedQuery("?ref=" + "4".repeat(80), find)).toBe("?ref=" + "4".repeat(40));
    const pdf = match("POST", ["verify", "pdf"])!;
    expect(forwardedQuery(`?title=Demanda&matter_id=${id}&tenant=x`, pdf)).toBe(`?title=Demanda&matter_id=${id}`);
    expect(forwardedQuery("?title=x&matter_id=not-a-uuid", pdf)).toBe("?title=x");
    const convs = match("GET", ["conversations"])!;
    expect(forwardedQuery(`?matter=${id}`, convs)).toBe(`?matter=${id}`);
    expect(forwardedQuery("?matter=../../x", convs)).toBe("");
    expect(forwardedQuery("?q=x", match("GET", ["matters"])!)).toBe("");
  });

  it("requires the PDF content type (a cross-site form cannot send it without a preflight)", () => {
    const h = (type: string) => new Headers({ "content-type": type, "sec-fetch-site": "same-origin" });
    expect(isSameOriginJson(h("application/pdf"), "pdf")).toBe(true);
    expect(isSameOriginJson(h("multipart/form-data"), "pdf")).toBe(false);
    expect(isSameOriginJson(h("application/json"), "pdf")).toBe(false);
    expect(isSameOriginJson(h("application/pdf"))).toBe(false);
  });
});

describe("diffWords (navigator history)", () => {
  const join = (side: "before" | "after", parts: ReturnType<typeof diffWords>) =>
    parts.filter((p) => p.kind === "same" || p.kind === (side === "before" ? "removed" : "added")).map((p) => p.text).join("");

  it("rebuilds both texts exactly and marks only what changed", () => {
    const a = "II. Incurrir el trabajador en faltas de probidad u honradez durante sus labores;";
    const b = "II. Incurrir el trabajador en faltas de probidad, honradez o acoso durante sus labores;";
    const parts = diffWords(a, b);
    expect(join("before", parts)).toBe(a);
    expect(join("after", parts)).toBe(b);
    expect(parts.filter((p) => p.kind === "added").map((p) => p.text.trim()).join(" ")).toContain("acoso");
    expect(parts.filter((p) => p.kind === "removed").map((p) => p.text.trim()).join(" ")).toContain("u");
  });

  it("handles identical, empty and fully different texts", () => {
    expect(diffWords("igual", "igual")).toEqual([{ kind: "same", text: "igual" }]);
    expect(diffWords("", "nuevo")).toEqual([{ kind: "added", text: "nuevo" }]);
    expect(diffWords("viejo", "")).toEqual([{ kind: "removed", text: "viejo" }]);
  });

  it("stays bounded on very long articles (falls back to lines)", () => {
    const long = Array.from({ length: 3000 }, (_, i) => `palabra${i}`).join(" ");
    const parts = diffWords(long, long + " fin");
    expect(join("after", parts)).toBe(long + " fin");
  });
});
