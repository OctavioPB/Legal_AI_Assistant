"use client";

import { useTranslations } from "next-intl";
import { Fragment, useId } from "react";
import { parseAnswer, type Block, type Inline } from "@/lib/answer";
import type { Citation } from "@/lib/api";
import { CitationChip, UnverifiedId } from "./CitationChip";

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
              return (
                <Fragment key={id}>
                  <CitationChip citeId={id} label={known.label} onOpen={onOpen} />
                  {/* Brand.md 4: the printed dossier keeps each citation as a numbered note */}
                  <sup className="print-only">{known.n}</sup>
                </Fragment>
              );
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

/** `compact`: inside a thread, where the sources panel already lists the citations, the answer
 *  shows neither its own title nor its sources list (the title stays for screen readers). */
export function AnswerView({
  text,
  citations,
  onOpen,
  compact = false,
}: {
  text: string;
  citations: Citation[];
  onOpen: (id: string) => void;
  compact?: boolean;
}) {
  const t = useTranslations("answer");
  const titleId = useId();
  const sourcesId = useId();
  const labels: Labels = new Map(citations.map((c, i) => [c.cite_id, { label: c.citation_string, n: i + 1 }]));
  const blocks = parseAnswer(text);
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
      {out}
      {!compact && (
        <section className="answer-sources" aria-labelledby={sourcesId}>
          <h3 id={sourcesId}>{t("citationsTitle")}</h3>
          {citations.length === 0 ? (
            <p>{t("noCitations")}</p>
          ) : (
            <ol className="source-list">
              {citations.map((c) => (
                <li key={c.cite_id}>
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
