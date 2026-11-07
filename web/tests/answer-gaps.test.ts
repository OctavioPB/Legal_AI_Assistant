import { describe, expect, it } from "vitest";
import { citedIds, parseAnswer } from "../src/lib/answer";

// Shapes produced by the REAL local model (gemma) that the first parser did not handle.
const REAL = [
  "(II) Marco normativo",
  "El artículo 47 establece las conductas [cite: MX-FED-LFT-ART47-CAPUT-v2026.11].",
  "",
  "(III) Criterios jurisprudenciales",
  "<insuficiente></insuficiente>",
  "",
  "(IV) Análisis",
  "[análisis propio]",
  "Es fundamental observar que la legislación distingue entre causales generales y especiales.",
  "",
  "(V) Fuentes citadas",
  "*   MX-FED-LFT-ART47-CAPUT-v2026.11: Ley Federal del Trabajo, art. 47",
].join("\n");

describe("answers as the local model really writes them", () => {
  const blocks = parseAnswer(REAL);

  it("turns an empty <insuficiente> block into a section gap, in place", () => {
    expect(blocks.map((b) => b.kind)).toEqual(["paragraph", "paragraph", "paragraph", "gap", "paragraph", "paragraph", "paragraph", "item"]);
  });

  it("a tag alone on its line marks the paragraph that follows it", () => {
    const analysis = blocks.find((b) => b.kind === "paragraph" && b.inline[0].kind === "text" && b.inline[0].text.startsWith("Es fundamental"));
    expect(analysis && "marked" in analysis ? analysis.marked : null).toBe("own");
    const before = blocks[1];
    expect("marked" in before ? before.marked : "x").toBeNull();
  });

  it("the label ends at a blank line", () => {
    const after = blocks.find((b) => b.kind === "paragraph" && b.inline[0].kind === "text" && b.inline[0].text.startsWith("(V)"));
    expect(after && "marked" in after ? after.marked : "x").toBeNull();
  });

  it("collects only real citation markers", () => {
    expect(citedIds(blocks)).toEqual(["MX-FED-LFT-ART47-CAPUT-v2026.11"]);
  });

  it("a strategic label line works too and text inside a gap never becomes content", () => {
    const b = parseAnswer("[razonamiento estratégico]\nTu teoría presume hechos no probados.\n<insuficiente>texto del modelo</insuficiente>");
    expect(b[0]).toMatchObject({ kind: "paragraph", marked: "strategic" });
    expect(b[1].kind).toBe("paragraph"); // a gap with text is not an empty gap: rendered as plain text, never as a refusal
  });
});
