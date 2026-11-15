"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { getModels, type ModelKey, type ModelOption, type ModelsInfo } from "@/lib/api";

/** The model profiles of the firm (ADR-024). Failing to load them leaves the server default. */
export function useModels(): ModelsInfo | null {
  const [info, setInfo] = useState<ModelsInfo | null>(null);
  useEffect(() => {
    let live = true;
    getModels()
      .then((m) => live && setInfo(m))
      .catch(() => live && setInfo(null));
    return () => {
      live = false;
    };
  }, []);
  return info;
}

/** What a new conversation may use: served here AND allowed by the firm. */
export function usableModels(info: ModelsInfo | null): ModelOption[] {
  return info ? info.options.filter((o) => o.available && o.allowed) : [];
}

export function isRemote(info: ModelsInfo | null, key: ModelKey | null | undefined): boolean {
  return Boolean(info?.options.find((o) => o.key === key)?.locality === "remote");
}

function MeasurementLine({ option }: { option: ModelOption }) {
  const t = useTranslations("workbench.model");
  const locale = useLocale();
  const m = option.measurement;
  if (!m) return <span className="small muted">{t("unmeasured")}</span>;
  const pct = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 });
  return (
    <span className="small muted">
      {t("measured", {
        date: m.measured_on,
        cases: m.cases,
        // retrieval recall is the same for every model; what differs is what the model does with it
        answered: pct.format(1 - m.false_refusal),
        refusal: pct.format(m.refusal_accuracy),
        precision: pct.format(m.citation_precision),
        seconds: Math.round(m.total_p50_s),
      })}
      {!m.reviewed_by_lawyer && ` (${t("notReviewed")})`}
    </span>
  );
}

/** Choice of model for a NEW conversation (Brand.md 9.6). Hidden when only one model is usable.
 *  A remote model shows, before anything is sent, what leaves the machine. */
export function ModelPicker({
  info,
  value,
  onChange,
  compact = false,
}: {
  info: ModelsInfo | null;
  value: ModelKey | null;
  onChange: (k: ModelKey) => void;
  compact?: boolean; // Brand.md 11.2: one select in the start page's box, same warning
}) {
  const t = useTranslations("workbench.model");
  const name = useId();
  const warnId = useId();
  const usable = usableModels(info);
  if (usable.length < 2) return null;
  const current = value ?? usable.find((o) => o.default)?.key ?? usable[0].key;
  const remote = isRemote(info, current);
  if (compact) {
    return (
      <div className="model-compact">
        <div className="field field-inline">
          <label htmlFor={name}>{t("label")}</label>
          <select id={name} value={current} aria-describedby={remote ? warnId : undefined} onChange={(e) => onChange(e.target.value as ModelKey)}>
            {usable.map((o) => (
              <option key={o.key} value={o.key}>
                {t(`name.${o.key}`)} · {t(o.locality)}
              </option>
            ))}
          </select>
        </div>
        {remote && (
          <p id={warnId} className="model-warning" role="note">
            <span aria-hidden="true">⚠ </span>
            {t("remoteWarning")}
          </p>
        )}
      </div>
    );
  }
  return (
    <fieldset className="model-picker" aria-describedby={remote ? warnId : undefined}>
      <legend>{t("label")}</legend>
      <p className="small muted">{t("help")}</p>
      {usable.map((o) => (
        <label key={o.key} className={o.key === current ? "model-option model-option-on" : "model-option"}>
          <input type="radio" name={name} value={o.key} checked={o.key === current} onChange={() => onChange(o.key)} />
          <span className="model-option-body">
            <span className="model-option-head">
              <strong>{t(`name.${o.key}`)}</strong>
              <span className={o.locality === "remote" ? "badge badge-warn" : "badge"}>{t(o.locality)}</span>
              {o.default && <span className="small muted">{t("default")}</span>}
            </span>
            <span className="small">{t(`summary.${o.key}`)}</span>
            <MeasurementLine option={o} />
          </span>
        </label>
      ))}
      {remote && (
        <p id={warnId} className="model-warning" role="note">
          <span aria-hidden="true">⚠ </span>
          {t("remoteWarning")}
        </p>
      )}
    </fieldset>
  );
}
