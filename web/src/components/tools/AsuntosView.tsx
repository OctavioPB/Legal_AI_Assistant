"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useState } from "react";
import {
  addMember,
  addNote,
  ApiError,
  createMatter,
  deleteMatter,
  deleteNote,
  deleteSource,
  getMatter,
  listColleagues,
  listMatterConversations,
  listMatters,
  removeMember,
  updateMatter,
  type Colleague,
  type Conversation,
  type Matter,
  type MatterDetail,
} from "@/lib/api";
import { useDateTime } from "@/lib/format";
import { SourceViewer } from "../SourceViewer";

const PAGE_OF: Record<Conversation["kind"], string> = { chat: "conversar", research: "investigacion", case: "argumentar" };

function readMatter(): string | null {
  const m = new URLSearchParams(window.location.search).get("m");
  return m && /^[0-9a-f-]{36}$/.test(m) ? m : null;
}

function writeMatter(id: string | null) {
  window.history.replaceState(null, "", id ? `${window.location.pathname}?m=${id}` : window.location.pathname);
}

/** Feature 5 (ADR-018, Brand.md 9.3): one folder per client matter, shared with chosen colleagues. */
export function AsuntosView() {
  const t = useTranslations("matters");
  const [matters, setMatters] = useState<Matter[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listMatters()
      .then(setMatters)
      .catch(() => setFailed(true));
  }, []);
  useEffect(() => {
    refresh();
    setOpen(readMatter());
  }, [refresh]);

  const show = (id: string | null) => {
    setOpen(id);
    writeMatter(id);
    if (id) requestAnimationFrame(() => document.getElementById("matter-title")?.focus());
  };

  return (
    <div className="tool-page">
      <header className="tool-head">
        <h1>{t("title")}</h1>
        <p className="muted">{t("intro")}</p>
      </header>
      {failed && (
        <p className="form-status" role="alert">
          {t("loadError")}
        </p>
      )}
      {open ? (
        <MatterPanel id={open} onBack={() => show(null)} onChanged={refresh} />
      ) : (
        <>
          <NewMatter onCreated={(m) => { refresh(); show(m.id); }} />
          <section aria-labelledby="matters-list">
            <h2 id="matters-list">{t("list")}</h2>
            {matters && matters.length === 0 && <p className="small muted">{t("empty")}</p>}
            {matters && matters.length > 0 && (
              <ul className="matter-list">
                {matters.map((m) => (
                  <li key={m.id}>
                    <button type="button" className="matter-card" onClick={() => show(m.id)}>
                      <span className="matter-card-title">{m.title}</span>
                      <span className="small muted">
                        {[m.client_ref, t(`role.${m.role}`), t("itemsCount", { n: m.items }), t("membersCount", { n: m.members }), m.status === "closed" ? t("closed") : null]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function NewMatter({ onCreated }: { onCreated: (m: Matter) => void }) {
  const t = useTranslations("matters");
  const ids = { title: useId(), ref: useId(), desc: useId() };
  const [title, setTitle] = useState("");
  const [ref, setRef] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="welcome"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!title.trim() || busy) return;
        setBusy(true);
        try {
          const m = await createMatter({ title: title.trim(), client_ref: ref.trim(), description: desc.trim() });
          setTitle("");
          setRef("");
          setDesc("");
          onCreated(m);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>{t("new")}</h2>
      <div className="field">
        <label htmlFor={ids.title}>{t("matterTitle")}</label>
        <input id={ids.title} value={title} maxLength={200} required placeholder={t("matterTitlePlaceholder")} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.ref}>{t("clientRef")}</label>
        <input id={ids.ref} value={ref} maxLength={200} placeholder={t("clientRefPlaceholder")} onChange={(e) => setRef(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.desc}>{t("description")}</label>
        <textarea id={ids.desc} value={desc} maxLength={4000} rows={3} onChange={(e) => setDesc(e.target.value)} />
      </div>
      <p className="small muted">{t("privacy")}</p>
      <button type="submit" className="button" disabled={busy || !title.trim()}>
        {t("create")}
      </button>
    </form>
  );
}

function MatterPanel({ id, onBack, onChanged }: { id: string; onBack: () => void; onChanged: () => void }) {
  const t = useTranslations("matters");
  const tr = useTranslations("reforms.change");
  const tk = useTranslations("nav");
  const when = useDateTime();
  const [d, setD] = useState<MatterDetail | null>(null);
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [colleagues, setColleagues] = useState<Colleague[]>([]);
  const [missing, setMissing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [note, setNote] = useState("");
  const [invite, setInvite] = useState("");
  const [viewing, setViewing] = useState<string | null>(null);
  const ids = { note: useId(), invite: useId() };

  const load = useCallback(async () => {
    try {
      const [detail, list] = await Promise.all([getMatter(id), listMatterConversations(id)]);
      setD(detail);
      setConvs(list);
      if (detail.matter.role === "owner") setColleagues(await listColleagues().catch(() => []));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
    }
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);

  if (missing) {
    return (
      <section>
        <p>{t("notFound")}</p>
        <button type="button" className="button button-quiet" onClick={onBack}>
          {t("back")}
        </button>
      </section>
    );
  }
  if (!d) return <p className="muted">{t("loading")}</p>;
  const owner = d.matter.role === "owner";
  const memberIds = new Set(d.members.map((x) => x.user_id));
  const candidates = colleagues.filter((c) => !memberIds.has(c.user_id));
  const act = async (fn: () => Promise<unknown>) => {
    await fn().catch(() => undefined);
    await load();
    onChanged();
  };

  return (
    <article className="matter" aria-labelledby="matter-title">
      <p>
        <button type="button" className="button button-quiet" onClick={onBack}>
          {t("back")}
        </button>
      </p>
      <header className="matter-head">
        <h2 id="matter-title" tabIndex={-1}>
          {d.matter.title}
        </h2>
        <p className="small muted">
          {[d.matter.client_ref, t(`role.${d.matter.role}`), d.matter.status === "closed" ? t("closed") : null].filter(Boolean).join(" · ")}
        </p>
        {d.matter.description && <p className="msg-text">{d.matter.description}</p>}
        {owner && (
          <div className="bar-actions">
            <button type="button" className="button button-quiet" onClick={() => act(() => updateMatter(id, { status: d.matter.status === "open" ? "closed" : "open" }))}>
              {d.matter.status === "open" ? t("close") : t("reopen")}
            </button>
            {confirm ? (
              <span className="bar-confirm" role="group" aria-label={t("confirmDelete")}>
                <span>{t("confirmDelete")}</span>
                <button
                  type="button"
                  className="button"
                  onClick={async () => {
                    await deleteMatter(id).catch(() => undefined);
                    onChanged();
                    onBack();
                  }}
                >
                  {t("delete")}
                </button>
                <button type="button" className="button button-quiet" onClick={() => setConfirm(false)}>
                  {t("cancel")}
                </button>
              </span>
            ) : (
              <button type="button" className="button button-quiet" onClick={() => setConfirm(true)}>
                {t("delete")}
              </button>
            )}
          </div>
        )}
      </header>

      <section aria-labelledby="matter-work">
        <h3 id="matter-work">{t("work")}</h3>
        {d.matter.status === "open" && (
          <p className="matter-new">
            {(["conversar", "investigacion", "argumentar"] as const).map((p) => (
              <Link key={p} className="button button-quiet" href={`/${p}?matter=${id}`}>
                {t("newIn", { page: tk(p) })}
              </Link>
            ))}
            <Link className="button button-quiet" href={`/verificar?matter=${id}`}>
              {t("verifyIn")}
            </Link>
            <Link className="button button-quiet" href={`/documentos?matter=${id}`}>
              {t("filesIn")}
            </Link>
          </p>
        )}
        {convs.length === 0 ? (
          <p className="small muted">{t("noWork")}</p>
        ) : (
          <ul className="matter-items">
            {convs.map((c) => (
              <li key={c.id}>
                <Link href={`/${PAGE_OF[c.kind]}?c=${c.id}`}>{c.title}</Link>
                <span className="small muted">
                  {" "}
                  · {tk(PAGE_OF[c.kind])} · {when(c.updated_at)}
                  {!c.mine && <> · {t("byColleague")}</>}
                </span>
              </li>
            ))}
          </ul>
        )}
        {d.reports.length > 0 && (
          <>
            <h4>{t("reports")}</h4>
            <ul className="matter-items">
              {d.reports.map((r) => (
                <li key={r.id}>
                  {r.title}
                  <span className="small muted">
                    {" "}
                    · {when(r.created_at)} · {t("reportCounts", { total: r.summary.total ?? 0, ok: r.summary.vigente ?? 0 })}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section aria-labelledby="matter-sources">
        <h3 id="matter-sources">{t("sources")}</h3>
        {d.sources.length === 0 ? (
          <p className="small muted">{t("noSources")}</p>
        ) : (
          <ul className="matter-items">
            {d.sources.map((s) => (
              <li key={s.id} className="citable">
                <p className="citable-head">
                  <button type="button" className="chip" title={s.cite_id} onClick={() => setViewing(s.cite_id)}>
                    <span aria-hidden="true">✓ </span>
                    {s.citation_string}
                  </button>
                  {s.change && (
                    <span className="badge badge-warn">
                      <span aria-hidden="true">◷ </span>
                      {tr(s.change)}
                    </span>
                  )}
                </p>
                {s.change && s.current_cite_id && (
                  <p className="small">
                    {t("reformedOpen")}{" "}
                    <button type="button" className="button button-quiet" onClick={() => setViewing(s.current_cite_id)}>
                      {t("openCurrent")}
                    </button>
                  </p>
                )}
                {s.note && <p className="small">{s.note}</p>}
                <p className="small muted">
                  {t("addedBy", { who: s.added_by })}{" "}
                  <button type="button" className="button button-quiet" onClick={() => act(() => deleteSource(id, s.id))}>
                    {t("remove")}
                  </button>
                </p>
              </li>
            ))}
          </ul>
        )}
        <p className="small muted">{t("sourcesHelp")}</p>
      </section>

      <section aria-labelledby="matter-notes">
        <h3 id="matter-notes">{t("notes")}</h3>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!note.trim()) return;
            await act(() => addNote(id, note.trim()));
            setNote("");
          }}
        >
          <div className="field">
            <label htmlFor={ids.note}>{t("newNote")}</label>
            <textarea id={ids.note} rows={3} maxLength={8000} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <button type="submit" className="button" disabled={!note.trim()}>
            {t("addNote")}
          </button>
        </form>
        <ul className="matter-items">
          {d.notes.map((n) => (
            <li key={n.id}>
              <p className="msg-text">{n.body}</p>
              <p className="small muted">
                {n.author} · {when(n.created_at)}{" "}
                {n.mine && (
                  <button type="button" className="button button-quiet" onClick={() => act(() => deleteNote(id, n.id))}>
                    {t("remove")}
                  </button>
                )}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="matter-members">
        <h3 id="matter-members">{t("members")}</h3>
        <ul className="matter-items">
          {d.members.map((x) => (
            <li key={x.user_id}>
              {x.display_name} <span className="small muted">· {x.email} · {t(`role.${x.role}`)}</span>{" "}
              {owner && x.role === "member" && (
                <button type="button" className="button button-quiet" onClick={() => act(() => removeMember(id, x.user_id))}>
                  {t("remove")}
                </button>
              )}
            </li>
          ))}
        </ul>
        {owner ? (
          candidates.length > 0 ? (
            <form
              className="inline-form"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!invite) return;
                await act(() => addMember(id, invite));
                setInvite("");
              }}
            >
              <div className="field">
                <label htmlFor={ids.invite}>{t("addMember")}</label>
                <select id={ids.invite} value={invite} onChange={(e) => setInvite(e.target.value)}>
                  <option value="">{t("choose")}</option>
                  {candidates.map((c) => (
                    <option key={c.user_id} value={c.user_id}>
                      {c.display_name} · {c.email}
                    </option>
                  ))}
                </select>
              </div>
              <button type="submit" className="button" disabled={!invite}>
                {t("add")}
              </button>
            </form>
          ) : (
            <p className="small muted">{t("noColleagues")}</p>
          )
        ) : null}
        <p className="small muted">{t("membersHelp")}</p>
      </section>
      <SourceViewer citeId={viewing} onClose={() => setViewing(null)} />
    </article>
  );
}
