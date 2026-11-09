"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { getSource, type Source } from "@/lib/api";
import { SaveToMatter } from "./tools/SaveToMatter";

type State = { kind: "loading" } | { kind: "error" } | { kind: "ready"; source: Source };

/** The text behind a citation, with the cited passage highlighted inside its whole article.
 *  Everything is rendered as text. Native <dialog>: focus trap and Esc come from the platform. */
export function SourceViewer({ citeId, onClose }: { citeId: string | null; onClose: () => void }) {
  const t = useTranslations("viewer");
  const ref = useRef<HTMLDialogElement>(null);
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (citeId && !dialog.open) dialog.showModal();
    if (!citeId && dialog.open) dialog.close();
  }, [citeId]);

  useEffect(() => {
    if (!citeId) return;
    let cancelled = false;
    setState({ kind: "loading" });
    getSource(citeId)
      .then((source) => !cancelled && setState({ kind: "ready", source }))
      .catch(() => !cancelled && setState({ kind: "error" }));
    return () => {
      cancelled = true;
    };
  }, [citeId]);

  return (
    <dialog ref={ref} className="viewer" aria-labelledby="viewer-title" onClose={onClose}>
      <header className="viewer-head">
        <h2 id="viewer-title">{t("title")}</h2>
        <button type="button" className="button button-quiet" onClick={onClose}>
          {t("close")}
        </button>
      </header>
      {state.kind === "loading" && <p>{t("loading")}</p>}
      {state.kind === "error" && <p>{t("unavailable")}</p>}
      {state.kind === "ready" && <SourceBody source={state.source} />}
    </dialog>
  );
}

function SourceBody({ source }: { source: Source }) {
  const t = useTranslations("viewer");
  const tc = useTranslations("reforms.change");
  const at = source.article_text.indexOf(source.text);
  const before = at >= 0 ? source.article_text.slice(0, at) : "";
  const after = at >= 0 ? source.article_text.slice(at + source.text.length) : "";
  return (
    <div className="viewer-body">
      <h3 className="viewer-cite">{source.citation_string}</h3>
      <p className="viewer-crumb">{source.breadcrumb}</p>
      <dl className="viewer-meta">
        <dt>{t("document")}</dt>
        <dd>{source.document_title}</dd>
        <dt>{t("version")}</dt>
        <dd>
          {source.version_label ?? "—"} ·{" "}
          <span className={source.is_current ? "badge" : "badge badge-warn"}>
            {source.is_current ? t("current") : t("notCurrent")}
          </span>
        </dd>
        <dt>{t("identifier")}</dt>
        <dd className="cite-id">{source.cite_id}</dd>
      </dl>
      {source.reform && (
        <p className="reform-notice" role="note">
          <span aria-hidden="true">◷ </span>
          {t("reformed", { change: tc(source.reform.change), date: source.reform.published_on ?? "—" })}
          {source.reform.current && <span className="cite-id"> {source.reform.current}</span>}
        </p>
      )}
      <p className="viewer-actions">
        <SaveToMatter citeId={source.cite_id} />
      </p>
      <h3>{t("article")}</h3>
      <div
        className="legal-text viewer-article"
        lang={source.cite_id.startsWith("US-") ? "en" : "es"}
        role="region"
        aria-label={t("article")}
        tabIndex={0}
      >
        {at >= 0 ? (
          <>
            {before}
            <mark className="cited-passage" aria-label={t("citedText")}>
              {source.text}
            </mark>
            {after}
          </>
        ) : (
          source.text
        )}
      </div>
    </div>
  );
}
