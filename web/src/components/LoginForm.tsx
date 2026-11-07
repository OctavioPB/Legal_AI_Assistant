"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

type Status = "idle" | "submitting" | "invalid" | "unavailable";

export function LoginForm() {
  const t = useTranslations("login");
  const [status, setStatus] = useState<Status>("idle");

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setStatus("submitting");
    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          tenant: form.get("tenant"),
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      if (res.ok) {
        window.location.assign("/");
        return;
      }
      setStatus(res.status === 401 ? "invalid" : "unavailable");
    } catch {
      setStatus("unavailable");
    }
  }

  return (
    <form className="login-card" onSubmit={onSubmit} aria-labelledby="login-title">
      <h1 id="login-title">{t("title")}</h1>
      {(status === "invalid" || status === "unavailable") && (
        <p className="form-status" role="alert">
          {t(`error.${status}`)}
        </p>
      )}
      <label className="field">
        <span>{t("tenant")}</span>
        <input name="tenant" type="text" autoComplete="organization" autoCapitalize="none" spellCheck={false} required />
      </label>
      <label className="field">
        <span>{t("email")}</span>
        <input name="email" type="email" autoComplete="username" required />
      </label>
      <label className="field">
        <span>{t("password")}</span>
        <input name="password" type="password" autoComplete="current-password" required />
      </label>
      <button className="button" type="submit" disabled={status === "submitting"}>
        {status === "submitting" ? t("submitting") : t("submit")}
      </button>
    </form>
  );
}
