import { describe, expect, it } from "vitest";
import type { Message } from "../src/lib/api";
import { plainWithNumbers } from "../src/lib/answer";
import { closingKind, conversationSources, sourceNumbers, splitClosingList, titleFrom } from "../src/lib/conversation";
import { DRAFT_TTL_MS, saveDraft, takeDraft } from "../src/lib/draft";
import { forwardedQuery, match } from "../src/lib/proxy";

const msg = (seq: number, role: "user" | "assistant", cites: string[] = [], status: Message["status"] = role === "assistant" ? "answered" : null): Message => ({
  seq, role, action: "ask", content: "x", status, refusal_reason: null,
  citations: cites.map((c) => ({ cite_id: c, citation_string: `L ${c}` })), request_id: null, created_at: "2026-11-08T00:00:00Z",
});

describe("titleFrom", () => {
  it("keeps short text, collapses whitespace and cuts long text at a word", () => {
    expect(titleFrom("  ¿Qué   dice\n el art. 47?  ")).toBe("¿Qué dice el art. 47?");
    const long = "palabra ".repeat(30);
    const t = titleFrom(long, 40);
    expect(t.endsWith("…")).toBe(true);
    expect(t.length).toBeLessThanOrEqual(41);
    expect(t).not.toMatch(/palabr…$/);
    expect(titleFrom("   ")).toBe("—");
  });
});

describe("conversationSources (Brand.md 11.3: one number per source in the whole conversation)", () => {
  it("lists each cited id once, in order of first appearance, ignoring refusals and user turns", () => {
    const m = [msg(1, "user"), msg(2, "assistant", ["A", "B"]), msg(3, "user"), msg(4, "assistant", ["C"], "refused"), msg(5, "user"), msg(6, "assistant", ["B", "D"])];
    const sources = conversationSources(m);
    expect(sources.map((c) => c.cite_id)).toEqual(["A", "B", "D"]);
    // a later answer citing B again keeps B's number: the panel and the answers agree
    expect([...sourceNumbers(sources)]).toEqual([["A", 1], ["B", 2], ["D", 3]]);
  });
});

describe("plainWithNumbers (Copiar con citas)", () => {
  it("replaces citations by their numbers, keeps unknown ids and drops bold markers", () => {
    const n = new Map([["X-1", 1], ["X-2", 2]]);
    const out = plainWithNumbers("**Causa** grave [cite: X-1; X-2]. Otra [cite: Z-9].", (id) => n.get(id));
    expect(out).toBe("Causa grave [1, 2]. Otra [Z-9].");
  });
});

describe("start-page draft (Brand.md 11.2)", () => {
  const store = new Map<string, string>();
  const g = globalThis as unknown as { window: { sessionStorage: Storage } };
  g.window = {
    sessionStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    } as Storage,
  };
  it("is read once, only by the workspace of its kind, and only while fresh", () => {
    const now = 1_000_000;
    saveDraft({ kind: "chat", text: "¿art. 47?", matter: null, model: "qwen", at: now });
    expect(takeDraft("research", now)).toBeNull(); // another workspace: dropped, never re-routed
    expect(takeDraft("chat", now)).toBeNull();
    saveDraft({ kind: "chat", text: "¿art. 47?", matter: null, model: "qwen", at: now });
    expect(takeDraft("chat", now + 1000)?.model).toBe("qwen");
    expect(takeDraft("chat", now + 1000)).toBeNull(); // already taken
    saveDraft({ kind: "case", text: "hechos", matter: null, model: null, at: now });
    expect(takeDraft("case", now + DRAFT_TTL_MS + 1)).toBeNull(); // stale
  });
});

describe("splitClosingList (Brand.md 3.3 panel)", () => {
  it("moves the last closing list out of the body, whatever the heading markup", () => {
    for (const heading of ["Vulnerabilidades priorizadas", "## Vulnerabilidades priorizadas", "**Vulnerabilidades priorizadas:**", "4. Vulnerabilidades priorizadas"]) {
      const text = `Ataque normativo [cite: A].\n\n${heading}\n1. Falta prueba del aviso [razonamiento estratégico]\n2. Otra [cite: B]`;
      const out = splitClosingList(text);
      expect(out.body).toBe("Ataque normativo [cite: A].");
      expect(out.list).toBe("1. Falta prueba del aviso [razonamiento estratégico]\n2. Otra [cite: B]");
      expect(closingKind(out.title)).toBe("vulnerabilities");
    }
  });
  it("recognizes the strengthen panel and English headings", () => {
    expect(closingKind(splitClosingList("a\nPuntos a reforzar\n- x").title)).toBe("reinforce");
    expect(closingKind(splitClosingList("a\n### Prioritized vulnerabilities\n- x").title)).toBe("vulnerabilities");
  });
  it("leaves the answer whole when the heading is absent, mid-sentence or has nothing after it", () => {
    for (const text of ["Sin lista final.", "Se discuten las vulnerabilidades priorizadas del caso [cite: A].", "Cuerpo\nVulnerabilidades priorizadas\n   "]) {
      expect(splitClosingList(text)).toEqual({ body: text, title: null, list: "" });
    }
  });
});

describe("gateway: conversation routes", () => {
  const id = "0b9d6f1e-2c3a-4e5f-8a9b-1c2d3e4f5a6b";
  it("forwards exactly the conversation routes with their methods", () => {
    expect(match("GET", ["conversations"])?.query).toBeDefined();
    expect(match("POST", ["conversations"])).not.toBeNull();
    for (const m of ["GET", "PATCH", "DELETE"]) expect(match(m, ["conversations", id])).not.toBeNull();
    expect(match("POST", ["conversations", id, "messages", "stream"])?.stream).toBe(true);
  });
  it("rejects ids that are not UUIDs, other methods and other sub-paths", () => {
    for (const [m, p] of [
      ["PUT", ["conversations", id]], ["DELETE", ["conversations"]], ["GET", ["conversations", "../traces"]],
      ["GET", ["conversations", "abc"]], ["POST", ["conversations", id, "messages"]], ["GET", ["conversations", id, "messages", "stream"]],
      ["PATCH", ["conversations", id.toUpperCase()]],
    ] as [string, string[]][]) {
      expect(match(m, p), `${m} ${p.join("/")}`).toBeNull();
    }
  });
  it("forwards only kind and q, and only a valid kind", () => {
    expect(forwardedQuery("?kind=chat&q=despido")).toBe("?kind=chat&q=despido");
    expect(forwardedQuery("?kind=admin&q=x&tenant=other")).toBe("?q=x");
    expect(forwardedQuery("?q=" + "x".repeat(500))).toBe("?q=" + "x".repeat(200));
    expect(forwardedQuery("")).toBe("");
  });
});
