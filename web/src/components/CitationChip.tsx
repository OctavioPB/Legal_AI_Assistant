"use client";

import { useTranslations } from "next-intl";

/** A verified citation (Brand.md 3.1): square chip, mono id on hover, check icon + visible label.
 *  Only ids the validator accepted are ever rendered as chips. */
export function CitationChip({ citeId, label, onOpen }: { citeId: string; label: string; onOpen: (id: string) => void }) {
  const t = useTranslations("cite");
  return (
    <button type="button" className="chip" title={citeId} onClick={() => onOpen(citeId)}>
      <span className="chip-icon" aria-hidden="true">
        ✓
      </span>
      <span className="sr-only">{t("verified")}: </span>
      {label}
      <span className="sr-only">. {t("open")}</span>
    </button>
  );
}

/** An id that is not in the verified list: shown as plain text, never with the chip look. */
export function UnverifiedId({ citeId }: { citeId: string }) {
  const t = useTranslations("cite");
  return (
    <span className="cite-id" title={t("unverified")}>
      {citeId}
    </span>
  );
}
