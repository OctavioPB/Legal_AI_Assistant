"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";

/** Page frame shared by the three workspaces (Brand.md 8.1): history | main | optional aside.
 *  The history can be hidden (state is per page view, not persisted). */
export function Workbench({
  title,
  failed,
  ready,
  history,
  aside,
  children,
}: {
  title: string;
  failed: boolean;
  ready: boolean;
  history: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  const t = useTranslations("workbench");
  const tw = useTranslations("workspace");
  const historyId = useId();
  const [showHistory, setShowHistory] = useState(true);
  useEffect(() => {
    // on a phone the history would push the conversation below the fold: start closed there
    if (window.matchMedia("(max-width: 64rem)").matches) setShowHistory(false);
  }, []);
  return (
    <div className={`workbench${aside ? " has-aside" : ""}${showHistory ? "" : " no-history"}`}>
      <div className="workbench-head">
        <h1>{title}</h1>
        <button type="button" className="button button-quiet" aria-controls={historyId} aria-expanded={showHistory} onClick={() => setShowHistory((v) => !v)}>
          {showHistory ? t("hideHistory") : t("showHistory")}
        </button>
      </div>
      <div id={historyId} className="workbench-history" hidden={!showHistory}>
        {history}
      </div>
      {/* not a live region: a whole answer would be re-read; RunningNote announces the stages */}
      <section className="workbench-main" aria-busy={!ready}>
        {failed && (
          <p className="form-status" role="alert">
            {tw("catalogError")}
          </p>
        )}
        {!ready && !failed && <p>{tw("loading")}</p>}
        {children}
      </section>
      {aside && <div className="workbench-aside">{aside}</div>}
    </div>
  );
}
