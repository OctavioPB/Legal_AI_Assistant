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

/** Brand.md 11.3: a verified citation inside the answer text, as its source number (the same number
 *  as in the sources panel). The citation string is its accessible name and tooltip, and is printed. */
export function CiteMark({ citeId, n, label, onOpen }: { citeId: string; n: number; label: string; onOpen: (id: string) => void }) {
  const t = useTranslations("cite");
  return (
    <>
      <button type="button" className="cite-mark" title={label} aria-label={t("mark", { n, label })} onClick={() => onOpen(citeId)}>
        {n}
      </button>
      <span className="print-only"> ({label})</span>
    </>
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
