// Brand.md 11.2: the start page's single box hands its text to the workspace of the chosen mode.
// It travels in sessionStorage (this tab only, read once), never in the URL: a URL lands in the
// browser history and in server logs, and the text can be privileged client content.
import type { Kind, ModelKey } from "./api";

export interface Draft {
  kind: Kind;
  text: string;
  matter: string | null;
  model: ModelKey | null;
  at: number; // ms epoch; an old draft (a tab restored hours later) is ignored
}

const KEY = "legalai.draft";
export const DRAFT_TTL_MS = 2 * 60 * 1000;

export function saveDraft(d: Draft): boolean {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(d));
    return true;
  } catch {
    return false; // storage blocked: the workspace opens empty, nothing is lost but the text
  }
}

/** The pending draft for this workspace, removed as it is read (it runs once). */
export function takeDraft(kind: Kind, now: number = Date.now()): Draft | null {
  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(KEY);
    if (raw) window.sessionStorage.removeItem(KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const d = JSON.parse(raw) as Draft;
    if (d.kind !== kind || typeof d.text !== "string" || !d.text.trim()) return null;
    if (!(now - d.at >= 0 && now - d.at <= DRAFT_TTL_MS)) return null;
    return d;
  } catch {
    return null;
  }
}
