"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

type Status = "idle" | "submitting" | "invalid" | "unavailable";

// Per-viewer convenience: the firm and the email are remembered on this browser so a returning
// user only types the password. The password itself is never stored anywhere by the page.
const REMEMBER_KEY = "legalai.login.v1";

function remembered(): { tenant: string; email: string } {
  try {
    const raw = window.localStorage.getItem(REMEMBER_KEY);
    const v = raw ? (JSON.parse(raw) as { tenant?: unknown; email?: unknown }) : {};
    return { tenant: typeof v.tenant === "string" ? v.tenant : "", email: typeof v.email === "string" ? v.email : "" };
  } catch {
    return { tenant: "", email: "" };
  }
}

export function LoginForm() {
  const t = useTranslations("login");
  const [status, setStatus] = useState<Status>("idle");
  const [tenant, setTenant] = useState("");
  const [email, setEmail] = useState("");
  const [show, setShow] = useState(false);
  const refs = { tenant: useRef<HTMLInputElement>(null), email: useRef<HTMLInputElement>(null), password: useRef<HTMLInputElement>(null) };

  useEffect(() => {
    const r = remembered();
    setTenant(r.tenant);
    setEmail(r.email);
    // the cursor starts in the first field still empty
    (r.tenant ? (r.email ? refs.password : refs.email) : refs.tenant).current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        try {
          window.localStorage.setItem(REMEMBER_KEY, JSON.stringify({ tenant: tenant.trim(), email: email.trim() }));
        } catch {
          /* storage blocked: nothing is remembered, login still works */
        }
        window.location.assign("/");
        return;
      }
      setStatus(res.status === 401 ? "invalid" : "unavailable");
      refs.password.current?.focus();
    } catch {
      setStatus("unavailable");
    }
  }

  return (
    <form className="login-card" onSubmit={onSubmit} aria-labelledby="login-title">
      <h1 id="login-title">{t("title")}</h1>
      <p className="small muted">{t("intro")}</p>
      {(status === "invalid" || status === "unavailable") && (
        <p className="form-status" role="alert">
          {t(`error.${status}`)}
        </p>
      )}
      <label className="field">
        <span>{t("tenant")}</span>
        <input ref={refs.tenant} name="tenant" type="text" autoComplete="organization" autoCapitalize="none" spellCheck={false} required value={tenant} onChange={(e) => setTenant(e.target.value)} />
      </label>
      <label className="field">
        <span>{t("email")}</span>
        <input ref={refs.email} name="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label className="field">
        <span>{t("password")}</span>
        <input ref={refs.password} name="password" type={show ? "text" : "password"} autoComplete="current-password" required />
      </label>
      <label className="check">
        <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />
        <span>{t("showPassword")}</span>
      </label>
      <button className="button" type="submit" disabled={status === "submitting"}>
        {status === "submitting" ? t("submitting") : t("submit")}
      </button>
      <p className="small muted">{t("help")}</p>
    </form>
  );
}
