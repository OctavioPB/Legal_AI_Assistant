// Pure helpers for the three workspaces (ADR-016). No React here: everything is unit-tested.
import type { Citation, Message } from "./api";

export const TITLE_MAX = 200;

/** A conversation title from the first message: one line, trimmed at a word, never empty. */
export function titleFrom(text: string, max = 80): string {
  const line = text.replace(/\s+/g, " ").trim();
  if (line.length <= max) return line || "—";
  const cut = line.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** Every source cited in the conversation, each id once, in order of first appearance: its
 *  position is its number, in the answers and in the sources panel alike (Brand.md 11.3). */
export function conversationSources(messages: Message[]): Citation[] {
  const seen = new Map<string, Citation>();
  for (const m of messages) {
    if (m.role !== "assistant" || m.status !== "answered") continue;
    for (const c of m.citations) if (!seen.has(c.cite_id)) seen.set(c.cite_id, c);
  }
  return [...seen.values()];
}

/** cite_id -> its number in the conversation (1-based). */
export function sourceNumbers(sources: Citation[]): Map<string, number> {
  return new Map(sources.map((c, i) => [c.cite_id, i + 1]));
}

export interface ClosingList {
  body: string; // the answer without the closing list
  title: string | null; // the heading as the model wrote it (e.g. "Vulnerabilidades priorizadas")
  list: string; // the list lines, kept verbatim (citations and marks inside are rendered later)
}

const CLOSING = /^[\s#>*_-]*(?:\d+[.)]\s*)?(vulnerabilidades priorizadas|puntos a reforzar|prioritized vulnerabilities|points to strengthen)\b[\s*_:.-]*$/i;

/** Brand.md 3.3: the closing "Vulnerabilidades priorizadas" / "Puntos a reforzar" of a sparring
 *  answer goes to its own panel. Only the LAST matching heading counts; its list is everything
 *  after it. Without that heading the answer is returned whole. */
export function splitClosingList(text: string): ClosingList {
  const lines = text.split("\n");
  let at = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (CLOSING.test(lines[i])) {
      at = i;
      break;
    }
  }
  if (at < 0) return { body: text, title: null, list: "" };
  const list = lines.slice(at + 1).join("\n").trim();
  if (!list) return { body: text, title: null, list: "" };
  const title = (CLOSING.exec(lines[at]) as RegExpExecArray)[1];
  return { body: lines.slice(0, at).join("\n").trimEnd(), title, list };
}

/** Which panel title applies: the user's side (strengthen) or the opponent's attack. */
export function closingKind(title: string | null): "vulnerabilities" | "reinforce" | null {
  if (!title) return null;
  return /refor|strengthen/i.test(title) ? "reinforce" : "vulnerabilities";
}
