"use client";

import { useTranslations } from "next-intl";
import { useId } from "react";

const KNOWN = [
  "retrieval.no_hits",
  "retrieval.below_threshold",
  "retrieval.no_version_as_of",
  "validation.model_declined",
  "validation.format_violation",
  "validation.unsupported",
] as const;

/** `<insuficiente>` as a first-class component (Brand.md 3.2): what is missing + what to do.
 *  Never red, never an error. The text comes from the catalog by `reason_key`, never from the
 *  model. */
export function RefusalCard({ reason }: { reason: string | null }) {
  const t = useTranslations("refusal");
  const titleId = useId();
  const known = (KNOWN as readonly string[]).includes(reason ?? "") ? (reason as (typeof KNOWN)[number]) : null;
  return (
    <section className="refusal" role="status" aria-labelledby={titleId}>
      <h2 id={titleId}>
        <span aria-hidden="true">⌕ </span>
        {t("title")}
      </h2>
      <p>{t("intro")}</p>
      <h3>{t("missingTitle")}</h3>
      <p>{known ? t(`reason.${known}`) : t("reason.validation.unsupported")}</p>
      <h3>{t("actionsTitle")}</h3>
      <ul>
        <li>{t("action.widen")}</li>
        <li>{t("action.rephrase")}</li>
        <li>{t("action.upload")}</li>
      </ul>
    </section>
  );
}
