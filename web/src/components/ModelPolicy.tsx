"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { getModels, setRemotePolicy, type ModelsInfo } from "@/lib/api";

/** Settings > External models (ADR-024, Brand.md 9.6). Only a firm admin changes it; everyone sees
 *  the state. Loaded when the settings panel opens, never on the login page. */
export function ModelPolicy() {
  const t = useTranslations("settings");
  const helpId = useId();
  const [info, setInfo] = useState<ModelsInfo | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  useEffect(() => {
    let live = true;
    getModels()
      .then((m) => live && setInfo(m))
      .catch(() => live && setInfo(null));
    return () => {
      live = false;
    };
  }, []);
  if (!info) return null;
  const claude = info.options.find((o) => o.key === "claude");
  return (
    <section className="settings-group" aria-labelledby="settings-models">
      <h3 id="settings-models">{t("models")}</h3>
      <label className="check">
        <input
          type="checkbox"
          checked={info.allow_remote}
          disabled={!info.can_manage || status === "saving"}
          aria-describedby={helpId}
          onChange={async (e) => {
            setStatus("saving");
            try {
              setInfo(await setRemotePolicy(e.target.checked));
              setStatus("saved");
            } catch {
              setStatus("failed");
            }
          }}
        />
        <span>{t("allowRemote")}</span>
      </label>
      <p id={helpId} className="small muted">
        {t("allowRemoteHelp")}
      </p>
      {claude && !claude.available && <p className="small muted">{t("remoteNotConfigured")}</p>}
      {!info.can_manage && <p className="small muted">{t("adminOnly")}</p>}
      <p className="small" role="status">
        {status === "saved" ? t("saved") : ""}
      </p>
    </section>
  );
}
