// Word-level comparison of two versions of a legal text (navigator history, Brand.md 9.1).
// Pure and deterministic: longest common subsequence over tokens that keep their whitespace,
// so joining the parts of each side gives back exactly the original text.

export type Part = { kind: "same" | "added" | "removed"; text: string };

const MAX_CELLS = 4_000_000; // beyond this (very long articles) the comparison is by line

function tokens(s: string, byLine: boolean): string[] {
  return byLine ? s.split(/(?<=\n)/) : (s.match(/\s+|[^\s]+/g) ?? []);
}

export function diffWords(before: string, after: string): Part[] {
  let a = tokens(before, false);
  let b = tokens(after, false);
  if (a.length * b.length > MAX_CELLS) {
    a = tokens(before, true);
    b = tokens(after, true);
    if (a.length * b.length > MAX_CELLS) return [{ kind: "removed", text: before }, { kind: "added", text: after }];
  }
  const n = a.length;
  const m = b.length;
  // lcs[i][j] = LCS length of a[i:] and b[j:]
  const w = m + 1;
  const lcs = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i * w + j] = a[i] === b[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
    }
  }
  const out: Part[] = [];
  const push = (kind: Part["kind"], text: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ kind, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push("same", a[i]);
      i++;
      j++;
    } else if (lcs[(i + 1) * w + j] >= lcs[i * w + j + 1]) {
      push("removed", a[i++]);
    } else {
      push("added", b[j++]);
    }
  }
  while (i < n) push("removed", a[i++]);
  while (j < m) push("added", b[j++]);
  return out;
}
