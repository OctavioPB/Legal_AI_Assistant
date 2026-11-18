// Turns the model's validated answer into a small typed tree. Rendering is done with React
// elements only (never raw HTML): statute text and model output are DATA (Brand.md section 3).

export type Mark = "strategic" | "own";

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; text: string }
  | { kind: "cite"; ids: string[] }
  | { kind: "mark"; mark: Mark };

export type Block =
  | { kind: "heading"; level: 1 | 2 | 3; inline: Inline[] }
  | { kind: "item"; inline: Inline[]; marked: Mark | null }
  | { kind: "paragraph"; inline: Inline[]; marked: Mark | null }
  | { kind: "gap" };

const GAP = /<insuficiente>\s*<\/insuficiente>/i;
const TOKEN = /\[\s*cite\s*:\s*([^\]\[]{0,2000})\]|\[(razonamiento estratégico|análisis propio)\]|\*\*([^*\n]{1,500})\*\*/gi;
const TAG_LINE = /^[\s*_#>-]*\[(razonamiento estratégico|análisis propio)\][\s*_]*$/i;

const markFrom = (name: string): Mark => (name.toLowerCase().startsWith("razon") ? "strategic" : "own");

export function parseInline(line: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of line.matchAll(TOKEN)) {
    if (m.index > last) out.push({ kind: "text", text: line.slice(last, m.index) });
    if (m[1] !== undefined) {
      const ids = [...new Set(m[1].split(/[;,]/).map((s) => s.trim()).filter(Boolean))];
      out.push({ kind: "cite", ids });
    } else if (m[2] !== undefined) {
      out.push({ kind: "mark", mark: markFrom(m[2]) });
    } else {
      out.push({ kind: "bold", text: m[3] });
    }
    last = (m.index ?? 0) + m[0].length;
  }
  if (last < line.length) out.push({ kind: "text", text: line.slice(last) });
  return out;
}

function markOf(inline: Inline[]): Mark | null {
  const m = inline.find((i) => i.kind === "mark");
  return m && m.kind === "mark" ? m.mark : null;
}

/** Headings (#), list items (-, *, 1.), paragraphs and section gaps. A tag alone on its line
 *  labels the paragraph(s) that follow it until a blank line (how the local model writes it). */
export function parseAnswer(text: string): Block[] {
  const blocks: Block[] = [];
  let label: Mark | null = null;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) {
      label = null;
      continue;
    }
    if (GAP.test(line) && line.replace(GAP, "").replace(/[\s*_-]/g, "") === "") {
      blocks.push({ kind: "gap" });
      continue;
    }
    const tag = TAG_LINE.exec(line);
    if (tag) {
      label = markFrom(tag[1]);
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      label = null;
      blocks.push({ kind: "heading", level: h[1].length as 1 | 2 | 3, inline: parseInline(h[2]) });
      continue;
    }
    const li = /^(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    const inline = parseInline(li ? li[1] : line);
    const marked = markOf(inline) ?? label;
    blocks.push(li ? { kind: "item", inline, marked } : { kind: "paragraph", inline, marked });
  }
  return blocks;
}

/** Every cite id mentioned, in order of first appearance. */
export function citedIds(blocks: Block[]): string[] {
  const seen = new Set<string>();
  for (const b of blocks) {
    if (!("inline" in b)) continue;
    for (const i of b.inline) if (i.kind === "cite") i.ids.forEach((id) => seen.add(id));
  }
  return [...seen];
}

const CITE = /\[\s*cite\s*:\s*([^\]\[]{0,2000})\]/gi;

/** Brand.md 11.3 "Copiar con citas": the answer as plain text, each citation replaced by its
 *  source number ([1], [1, 2]); ids the validator did not accept stay as written. Bold markers go;
 *  the reasoning tags stay (they say the line has no normative support). */
export function plainWithNumbers(text: string, numberOf: (id: string) => number | undefined): string {
  return text
    .replace(CITE, (_m, ids: string) => {
      const parts = [...new Set(ids.split(/[;,]/).map((s) => s.trim()).filter(Boolean))].map((id) => numberOf(id) ?? id);
      return `[${parts.join(", ")}]`;
    })
    .replace(/\*\*([^*\n]{1,500})\*\*/g, "$1");
}
