"use client";

import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { listMatters, saveSource, type Matter } from "@/lib/api";

/** "Save to matter" for one citable unit (Brand.md 9.3): the matters load only when asked. */
export function SaveToMatter({ citeId }: { citeId: string }) {
  const t = useTranslations("matters.save");
  const id = useId();
  const [open, setOpen] = useState(false);
  const [matters, setMatters] = useState<Matter[] | null>(null);
  const [pick, setPick] = useState("");
  const [state, setState] = useState<"idle" | "saved" | "error">("idle");

  if (!open) {
    return (
      <span className="save-matter">
      <button
        type="button"
        className="button button-quiet"
        onClick={async () => {
          setOpen(true);
          setState("idle");
          const m = await listMatters().catch(() => []);
          const usable = m.filter((x) => x.status === "open");
          setMatters(usable);
          setPick(usable[0]?.id ?? "");
        }}
      >
        {t("open")}
      </button>
      {state === "saved" && (
        <span className="small" role="status">
          {t("saved")}
        </span>
      )}
      </span>
    );
  }
  return (
    <span className="save-matter" role="group" aria-label={t("open")}>
      {matters === null ? (
        <span className="small muted">{t("loading")}</span>
      ) : matters.length === 0 ? (
        <span className="small muted">{t("noMatters")}</span>
      ) : (
        <>
          <label htmlFor={id} className="sr-only">
            {t("which")}
          </label>
          <select id={id} value={pick} onChange={(e) => setPick(e.target.value)}>
            {matters.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="button"
            disabled={!pick}
            onClick={async () => {
              try {
                await saveSource(pick, citeId);
                setState("saved");
                setOpen(false);
              } catch {
                setState("error");
              }
            }}
          >
            {t("save")}
          </button>
        </>
      )}
      <button type="button" className="button button-quiet" onClick={() => setOpen(false)}>
        {t("cancel")}
      </button>
      {state === "error" && (
        <span className="small" role="status">
          {t("error")}
        </span>
      )}
    </span>
  );
}
