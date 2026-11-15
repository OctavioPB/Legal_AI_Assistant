"use client";

import { useTranslations } from "next-intl";
import { Fragment, useId, useState } from "react";
import { citedIds, parseAnswer, plainWithNumbers, type Block, type Inline } from "@/lib/answer";
import type { Citation } from "@/lib/api";
import { CitationChip, CiteMark, UnverifiedId } from "./CitationChip";

type Labels = Map<string, { label: string; n: number }>;

function Inlines({ items, labels, onOpen }: { items: Inline[]; labels: Labels; onOpen: (id: string) => void }) {
  return (
    <>
      {items.map((it, i) => {
        if (it.kind === "text") return <Fragment key={i}>{it.text}</Fragment>;
        if (it.kind === "bold") return <strong key={i}>{it.text}</strong>;
        if (it.kind === "mark") return null; // the mark is shown by the block wrapper
        return (
          <span key={i} className="cite-group">
            {it.ids.map((id) => {
              const known = labels.get(id);
              if (!known) return <UnverifiedId key={id} citeId={id} />;
              return <CiteMark key={id} citeId={id} n={known.n} label={known.label} onOpen={onOpen} />;
            })}
          </span>
        );
      })}
    </>
  );
}

/** Model-added content (Brand.md 3): shaded, dashed, italic, labelled; never looks like a citation. */
function Reasoning({ mark, children }: { mark: "strategic" | "own"; children: React.ReactNode }) {
  const t = useTranslations("mark");
  return (
    <div className="reasoning" data-mark={mark}>
      <p className="reasoning-label">
        <span aria-hidden="true">◇ </span>
        {t(`${mark}.label`)} — {t(`${mark}.note`)}
      </p>
      <div className="reasoning-body">{children}</div>
    </div>
  );
}

/** A section for which no source was retrieved: the system's own text (catalog), never the model's. */
function SectionGap() {
  const t = useTranslations("answer");
  return (
    <p className="gap" role="note">
      <span aria-hidden="true">⌕ </span>
      {t("gap")}
    </p>
  );
}

function BlockView({ block, labels, onOpen }: { block: Block; labels: Labels; onOpen: (id: string) => void }) {
  if (block.kind === "gap") return <SectionGap />;
  const inner = <Inlines items={block.inline} labels={labels} onOpen={onOpen} />;
  if (block.kind === "heading") {
    const H = (`h${block.level + 1}` as "h2" | "h3" | "h4");
    return <H className="answer-heading">{inner}</H>;
  }
  const body = block.marked ? <Reasoning mark={block.marked}>{inner}</Reasoning> : inner;
  return block.kind === "item" ? <li>{body}</li> : <p className="answer-text">{body}</p>;
}

/** Brand.md 11.3: what the system checked, above the answer. Counts only verified citations and
 *  the paragraphs marked as the assistant's own analysis; never "N of M claims". */
function VerifyStrip({ verified, reasoning }: { verified: number; reasoning: number }) {
  const t = useTranslations("answer");
  if (verified === 0) return null;
  return (
    <p className="verify-strip">
      <span className="verify-ok">
        <span aria-hidden="true">✓ </span>
        {t("verified", { count: verified })}
      </span>
      {reasoning > 0 && <span className="verify-own"> · {t("reasoningCount", { count: reasoning })}</span>}
    </p>
  );
}

/** "Copiar con citas": plain text with [n] markers, then the numbered sources. */
function CopyButton({ text, citations, labels }: { text: string; citations: Citation[]; labels: Labels }) {
  const t = useTranslations("answer");
  const [done, setDone] = useState(false);
  const copy = async () => {
    const used = citations.filter((c) => labels.has(c.cite_id)).sort((a, b) => labels.get(a.cite_id)!.n - labels.get(b.cite_id)!.n);
    const body = plainWithNumbers(text, (id) => labels.get(id)?.n);
    const notes = used.map((c) => `[${labels.get(c.cite_id)!.n}] ${c.citation_string}`).join("\n");
    try {
      await navigator.clipboard.writeText(notes ? `${body}\n\n${t("copySources")}:\n${notes}` : body);
      setDone(true);
      window.setTimeout(() => setDone(false), 4000);
    } catch {
      setDone(false);
    }
  };
  return (
    <p className="answer-actions no-print">
      <button type="button" className="button button-quiet" onClick={() => void copy()}>
        {t("copy")}
      </button>
      <span className="small muted" role="status">
        {done ? t("copied") : ""}
      </span>
    </p>
  );
}

/** `compact`: inside a thread, where the sources panel already lists the citations, the answer
 *  shows neither its own title nor its sources list (the title stays for screen readers).
 *  `numbers`: the conversation-wide source numbers (Brand.md 11.3); default = order in this answer. */
export function AnswerView({
  text,
  citations,
  onOpen,
  compact = false,
  numbers,
}: {
  text: string;
  citations: Citation[];
  onOpen: (id: string) => void;
  compact?: boolean;
  numbers?: Map<string, number>;
}) {
  const t = useTranslations("answer");
  const titleId = useId();
  const sourcesId = useId();
  const labels: Labels = new Map(citations.map((c, i) => [c.cite_id, { label: c.citation_string, n: numbers?.get(c.cite_id) ?? i + 1 }]));
  const blocks = parseAnswer(text);
  const verified = citedIds(blocks).filter((id) => labels.has(id)).length;
  const reasoning = blocks.filter((b) => (b.kind === "paragraph" || b.kind === "item") && b.marked).length;
  const out: React.ReactNode[] = [];
  for (let i = 0; i < blocks.length; ) {
    if (blocks[i].kind === "item") {
      const items: React.ReactNode[] = [];
      for (; i < blocks.length && blocks[i].kind === "item"; i++) {
        items.push(<BlockView key={i} block={blocks[i]} labels={labels} onOpen={onOpen} />);
      }
      out.push(<ul key={`ul${i}`}>{items}</ul>);
    } else {
      out.push(<BlockView key={i} block={blocks[i]} labels={labels} onOpen={onOpen} />);
      i++;
    }
  }
  return (
    <article className={compact ? "answer answer-compact" : "answer"} aria-labelledby={titleId}>
      <h2 id={titleId} className={compact ? "sr-only" : undefined}>
        {t("title")}
      </h2>
      <VerifyStrip verified={verified} reasoning={reasoning} />
      {out}
      <CopyButton text={text} citations={citations} labels={labels} />
      {!compact && (
        <section className="answer-sources" aria-labelledby={sourcesId}>
          <h3 id={sourcesId}>{t("citationsTitle")}</h3>
          {citations.length === 0 ? (
            <p>{t("noCitations")}</p>
          ) : (
            <ol className="source-list">
              {citations.map((c) => (
                <li key={c.cite_id} value={labels.get(c.cite_id)?.n}>
                  <CitationChip citeId={c.cite_id} label={c.citation_string} onOpen={onOpen} />
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </article>
  );
}
