"""Laws found in official indexes, and the operator's decision on each (ADR-026).

    uv run python -m app.ingestion.discover list [--status new] [--search trabajo]
    uv run python -m app.ingestion.discover approve DIP_FED:LGAg --domain environmental --by "Ruiz"
    uv run python -m app.ingestion.discover dismiss DIP_FED:LFCA --by "Ruiz"

The reform watcher reads the indexes (`app.ingestion.watch`, daily) and records their entries here;
it never downloads a law because an index lists it. An approval turns one entry into a watched
source (`corpus_sources`): jurisdiction, scope, language and grammar come from the index's closed
spec, the legal domains from the closed catalog, the cite_id code from the official file name.
The watcher then ingests its first version and, from then on, its reforms.

Approval is an operator action, not a firm's: the public corpus is shared by every firm, and only
`corpus_ingestor` (never the API's role) may write it (ADR-010 c).
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.corpus import CORPUS, LEGAL_DOMAINS, CorpusDoc
from app.ingestion.indexes import INDEXES, IndexEntry, IndexSpec
from app.settings import get_settings

DOC_TYPES = ("codigo", "ley", "reglamento")
_FIRST_WORD = {"LEY": "ley", "CODIGO": "codigo", "REGLAMENTO": "reglamento"}


class DecisionError(ValueError):
    """An approval or dismissal that cannot be made (unknown entry, already decided, bad input)."""


def _fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).upper()


def record_index(
    conn: Connection, spec: IndexSpec, entries: Iterable[IndexEntry]
) -> dict[str, int]:
    """Upsert the entries of one read of an index. The FIRST read files everything as `available`
    (the catalog as it was found); an entry that appears in a later read is `new`. An entry absent
    from this read gets `missing_since` (it is never deleted: a law leaves the index when it is
    abrogated, which is information)."""
    entries = list(entries)
    known = {
        r.entry_key: r
        for r in conn.execute(
            text("SELECT entry_key, missing_since FROM corpus_index_entries WHERE index_id = :i"),
            {"i": spec.index_id},
        )
    }
    first = not known
    counts = {"entries": len(entries), "new": 0, "missing": 0, "returned": 0}
    for e in entries:
        if e.key not in known and not first:
            counts["new"] += 1
        elif e.key in known and known[e.key].missing_since is not None:
            counts["returned"] += 1
    if entries:
        conn.execute(
            text(
                "INSERT INTO corpus_index_entries (tenant_id, index_id, entry_key, title, url, "
                "published_on, last_reform_on, last_reform_text, note, status) VALUES "
                "(public_corpus_id(), :i, :k, :t, :u, :p, :r, :rt, :n, :s) "
                "ON CONFLICT (tenant_id, index_id, entry_key) DO UPDATE SET "
                "title = EXCLUDED.title, url = EXCLUDED.url, published_on = EXCLUDED.published_on, "
                "last_reform_on = EXCLUDED.last_reform_on, "
                "last_reform_text = EXCLUDED.last_reform_text, note = EXCLUDED.note, "
                "last_seen_at = now(), missing_since = NULL"
            ),
            [
                {
                    "i": spec.index_id,
                    "k": e.key,
                    "t": e.title[:600],
                    "u": e.url,
                    "p": e.published_on,
                    "r": e.last_reform_on,
                    "rt": e.last_reform_text[:200] or None,
                    "n": e.note[:300] or None,
                    "s": "available" if first else "new",
                }
                for e in entries
            ],
        )
    gone = conn.execute(
        text(
            "UPDATE corpus_index_entries SET missing_since = now() WHERE index_id = :i "
            "AND missing_since IS NULL AND NOT (entry_key = ANY(:keys)) RETURNING entry_key"
        ),
        {"i": spec.index_id, "keys": [e.key for e in entries]},
    ).all()
    counts["missing"] = len(gone)
    return counts


def approved_sources(conn: Connection) -> tuple[CorpusDoc, ...]:
    rows = conn.execute(
        text(
            "SELECT key, title, source_url, country, level, state_code, municipality, scope, "
            "code, language, doc_type, legal_domain, grammar FROM corpus_sources "
            "ORDER BY approved_at, key"
        )
    ).all()
    return tuple(
        CorpusDoc(
            key=r.key,
            folder="raw",
            country=r.country,
            scope=r.scope,
            code=r.code,
            language=r.language,
            doc_type=r.doc_type,
            title=r.title,
            jurisdiction=(r.country, r.level, r.state_code, r.municipality),
            legal_domain=tuple(r.legal_domain),
            grammar=r.grammar,
            source_url=r.source_url,
            approved=True,
        )
        for r in rows
    )


def watched_sources(conn: Connection) -> tuple[CorpusDoc, ...]:
    """The static registry plus every approved source: what the reform watcher checks."""
    return CORPUS + approved_sources(conn)


def code_for(entry_key: str) -> str:
    """cite_id code segment from the official file name: letters and digits, upper case, never
    starting with a digit ("LGAg" -> "LGAG", "LSInt_300519" -> "LSINT300519", "79" -> "L79")."""
    code = re.sub(r"[^A-Za-z0-9]", "", entry_key).upper()
    return f"L{code}" if code[:1].isdigit() else code


def doc_type_for(title: str) -> str | None:
    words = _fold(title).split()
    return _FIRST_WORD.get(words[0]) if words else None


def display_title(title: str) -> str:
    """The index writes the first word in capitals ("LEY Federal del Trabajo")."""
    first, _, rest = title.partition(" ")
    return (first[:1] + first[1:].lower() + (" " + rest if rest else "")).strip()


def _entry(conn: Connection, index_id: str, key: str) -> Any:
    row = conn.execute(
        text(
            "SELECT entry_key, title, url, status, missing_since FROM corpus_index_entries "
            "WHERE index_id = :i AND entry_key = :k"
        ),
        {"i": index_id, "k": key},
    ).one_or_none()
    if row is None:
        raise DecisionError(f"{index_id}:{key} is not in the index (run the watcher first)")
    if row.status in ("approved", "dismissed"):
        raise DecisionError(f"{index_id}:{key} was already {row.status}")
    return row


def approve(
    conn: Connection,
    index_id: str,
    key: str,
    *,
    domains: Iterable[str],
    by: str,
    doc_type: str | None = None,
) -> CorpusDoc:
    spec = INDEXES.get(index_id)
    if spec is None:
        raise DecisionError(f"unknown index {index_id!r}: {', '.join(INDEXES)}")
    row = _entry(conn, index_id, key)
    if row.missing_since is not None:
        raise DecisionError(f"{index_id}:{key} is no longer listed by the index")
    doms = sorted(set(domains))
    if not doms or len(doms) > 4 or any(d not in LEGAL_DOMAINS for d in doms):
        raise DecisionError(f"1 to 4 legal domains from: {', '.join(LEGAL_DOMAINS)}")
    by = " ".join(by.split())
    if not by or len(by) > 120:
        raise DecisionError("--by: who approves it (1-120 characters)")
    dtype = doc_type or doc_type_for(row.title)
    if dtype not in DOC_TYPES:
        raise DecisionError(
            f"the title does not say whether it is a ley, código or reglamento: "
            f"pass --doc-type ({', '.join(DOC_TYPES)})"
        )
    code = code_for(key)
    country = spec.jurisdiction[0]
    if any(d.source_url == row.url for d in CORPUS):
        raise DecisionError(f"{row.url} is already watched")
    clash = [d.key for d in CORPUS if (d.country, d.scope, d.code) == (country, spec.scope, code)]
    clash += (
        conn.execute(
            text(
                "SELECT key FROM corpus_sources "
                "WHERE (country, scope, code) = (:c, :s, :k) OR key = :k"
            ),
            {"c": country, "s": spec.scope, "k": code},
        )
        .scalars()
        .all()
    )
    if clash or code in {d.key for d in CORPUS}:
        raise DecisionError(f"the code {code} is already used by {clash or code}")
    c, lv, st, mu = spec.jurisdiction
    conn.execute(
        text(
            "INSERT INTO corpus_sources (tenant_id, key, index_id, entry_key, title, source_url, "
            "country, level, state_code, municipality, scope, code, language, doc_type, "
            "legal_domain, grammar, approved_by) VALUES (public_corpus_id(), :key, :i, :e, :t, :u, "
            ":c, :lv, :st, :mu, :sc, :code, :lang, :dt, :dom, :g, :by)"
        ),
        {
            "key": code,
            "i": index_id,
            "e": key,
            "t": display_title(row.title),
            "u": row.url,
            "c": c,
            "lv": lv,
            "st": st,
            "mu": mu,
            "sc": spec.scope,
            "code": code,
            "lang": spec.language,
            "dt": dtype,
            "dom": doms,
            "g": spec.grammar,
            "by": by,
        },
    )
    conn.execute(
        text(
            "UPDATE corpus_index_entries SET status = 'approved', source_key = :k, "
            "decided_by = :by, decided_at = now() WHERE index_id = :i AND entry_key = :e"
        ),
        {"k": code, "by": by, "i": index_id, "e": key},
    )
    [doc] = [d for d in approved_sources(conn) if d.key == code]
    return doc


def dismiss(conn: Connection, index_id: str, key: str, *, by: str) -> None:
    _entry(conn, index_id, key)
    by = " ".join(by.split())
    if not by or len(by) > 120:
        raise DecisionError("--by: who dismisses it (1-120 characters)")
    conn.execute(
        text(
            "UPDATE corpus_index_entries SET status = 'dismissed', decided_by = :by, "
            "decided_at = now() WHERE index_id = :i AND entry_key = :e"
        ),
        {"by": by, "i": index_id, "e": key},
    )


def _split(ref: str) -> tuple[str, str]:
    index_id, sep, key = ref.partition(":")
    if not sep or not key:
        raise DecisionError(f"{ref!r}: expected INDEX:KEY, e.g. DIP_FED:LGAg")
    return index_id, key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.ingestion.discover")
    ap.add_argument("--database", help="database name (default: legalai)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="entries of the official indexes")
    ls.add_argument("--status", choices=("new", "available", "approved", "dismissed"))
    ls.add_argument("--search", help="words of the title (accents and case ignored)")
    ok = sub.add_parser("approve", help="watch and load a law of an index")
    ok.add_argument("ref", help="INDEX:KEY, as `list` prints it")
    ok.add_argument("--domain", required=True, help="comma-separated: " + ",".join(LEGAL_DOMAINS))
    ok.add_argument("--by", required=True, help="who approves it")
    ok.add_argument("--doc-type", choices=DOC_TYPES)
    no = sub.add_parser("dismiss", help="do not load a law the index lists")
    no.add_argument("ref")
    no.add_argument("--by", required=True)
    a = ap.parse_args(argv)
    engine = create_engine(get_settings().ingestor_url(a.database))
    try:
        with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            if a.cmd == "list":
                return _list(conn, a.status, a.search)
            index_id, key = _split(a.ref)
            if a.cmd == "approve":
                d = approve(conn, index_id, key, domains=a.domain.split(","), by=a.by,
                            doc_type=a.doc_type)  # fmt: skip
                sys.stdout.write(
                    f"approved {d.key}: {d.title} ({d.doc_type}, {', '.join(d.legal_domain)})\n"
                    "the next watcher run downloads and ingests it "
                    "(now: python -m app.ingestion.watch --only " + d.key + ")\n"
                )
            else:
                dismiss(conn, index_id, key, by=a.by)
                sys.stdout.write(f"dismissed {index_id}:{key}\n")
    except DecisionError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    finally:
        engine.dispose()
    return 0


def _list(conn: Connection, status: str | None, search: str | None) -> int:
    rows = conn.execute(
        text(
            "SELECT index_id, entry_key, title, url, status, last_reform_text, note, "
            "missing_since, source_key FROM corpus_index_entries "
            "WHERE (CAST(:s AS text) IS NULL OR status = :s) "
            "ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, "
            "index_id, title"
        ),
        {"s": status},
    ).all()
    words = _fold(search or "").split()
    watched = {d.source_url for d in CORPUS}
    n = 0
    for r in rows:
        if words and not all(w in _fold(r.title) for w in words):
            continue
        n += 1
        flags = [r.status]
        if r.missing_since is not None:
            flags.append("no longer listed")
        if r.source_key:
            flags.append(f"watched as {r.source_key}")
        if r.url in watched:
            flags.append("loaded")
        sys.stdout.write(
            f"{r.index_id}:{r.entry_key:24} {', '.join(flags):24} {r.title[:90]}"
            f"  [{r.last_reform_text or '-'}]{'  ' + r.note if r.note else ''}\n"
        )
    sys.stdout.write(f"\n{n} entr{'y' if n == 1 else 'ies'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DecisionError", "approve", "approved_sources", "code_for", "dismiss", "display_title",
    "doc_type_for", "record_index", "watched_sources",
]  # fmt: skip
