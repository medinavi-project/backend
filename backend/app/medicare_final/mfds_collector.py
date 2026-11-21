"""Explicit, bounded MFDS collection. No network access on import."""

import argparse
import csv
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from .mfds_api import MFDSClient, MFDSError, EASY, PERMIT
from .data_processor import SOURCES


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(path):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS records (source TEXT, item_seq TEXT, payload TEXT NOT NULL,
        retrieved_at TEXT NOT NULL, PRIMARY KEY(source,item_seq));
    CREATE TABLE IF NOT EXISTS checkpoints (source TEXT PRIMARY KEY, next_page INTEGER,
        page_size INTEGER, total INTEGER, complete INTEGER, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS runs (started_at TEXT, finished_at TEXT, source TEXT,
        calls INTEGER, pages INTEGER, duplicates INTEGER, invalid INTEGER, status TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS request_events (recorded_at TEXT, source TEXT, stage TEXT,
        detail TEXT, exception_type TEXT, http_status INTEGER, elapsed_seconds REAL);
    """)
    return db


def _save_diagnostics(db, client, source, start):
    events = getattr(client, "request_diagnostics", [])
    if not isinstance(events, list):
        return
    with db:
        for event in events[start:]:
            db.execute(
                "INSERT INTO request_events VALUES (?,?,?,?,?,?,?)",
                (
                    now(),
                    source,
                    event.get("stage"),
                    event.get("detail"),
                    event.get("exception_type"),
                    event.get("http_status"),
                    event.get("elapsed_seconds"),
                ),
            )


def collect(db, client, source, *, mode="initial", max_pages=1, page_size=100):
    """Collect one source; resume checkpoint for initial, restart bounded scan for update.

    Page transactions retain previous cache on failure. Existing records are not deleted,
    so partial refreshes cannot establish that absent products have been removed.
    """
    if (
        source not in ("permit", "easy")
        or mode not in ("initial", "update")
        or max_pages < 1
    ):
        raise ValueError("invalid collection arguments")
    checkpoint = db.execute(
        "SELECT next_page,page_size,complete FROM checkpoints WHERE source=?", (source,)
    ).fetchone()
    if mode == "initial" and checkpoint:
        if checkpoint[1] != page_size:
            raise ValueError("resume requires the original page size")
        if checkpoint[2]:
            return {
                "source": source,
                "status": "complete",
                "calls": 0,
                "pages": 0,
                "duplicates": 0,
                "invalid": 0,
            }
        page = checkpoint[0]
    else:
        page = 1
    started, before = now(), client.calls
    diagnostic_start = (
        len(getattr(client, "request_diagnostics", []))
        if isinstance(getattr(client, "request_diagnostics", []), list)
        else 0
    )
    pages = duplicates = invalid = 0
    status, error, total = "partial", None, None
    path = EASY if source == "easy" else PERMIT + "getDrugPrdtPrmsnInq08"
    field = "itemSeq" if source == "easy" else "ITEM_SEQ"
    try:
        for _ in range(max_pages):
            result = client._request(path, {}, page, page_size)
            total = result.total_count
            if not result.items and (page - 1) * page_size < total:
                raise MFDSError("api", "페이지가 전체 건수보다 일찍 끝났습니다.")
            stamp = now()
            with db:
                for row in result.items:
                    seq = str(row.get(field) or "").strip()
                    if not seq or not seq.isdigit():
                        invalid += 1
                        continue
                    if db.execute(
                        "SELECT 1 FROM records WHERE source=? AND item_seq=?",
                        (source, seq),
                    ).fetchone():
                        duplicates += 1
                    db.execute(
                        "INSERT OR REPLACE INTO records VALUES (?,?,?,?)",
                        (source, seq, json.dumps(row, ensure_ascii=False), stamp),
                    )
                complete = not result.items or page * page_size >= total
                db.execute(
                    "INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?)",
                    (source, page + 1, page_size, total, int(complete), stamp),
                )
            pages += 1
            page += 1
            if complete:
                status = "complete"
                break
    except MFDSError as exc:
        status, error = "error", exc.category
    calls = client.calls - before
    _save_diagnostics(db, client, source, diagnostic_start)
    with db:
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
            (started, now(), source, calls, pages, duplicates, invalid, status, error),
        )
    return {
        "source": source,
        "status": status,
        "error": error,
        "calls": calls,
        "pages": pages,
        "duplicates": duplicates,
        "invalid": invalid,
        "total_reported": total,
        "next_page": page,
    }


def collect_item(db, client, item_seq):
    """Fetch complete first pages for one official code; commit each successful source."""
    import re

    if not re.fullmatch(r"\d{9}", str(item_seq)):
        raise ValueError("9자리 품목코드가 필요합니다.")
    results = []
    for source, method in (
        ("permit", client.permit_list),
        ("permit_detail", client.permit_detail),
        ("easy", client.easy),
        ("ingredients", client.ingredients),
    ):
        before = client.calls
        diagnostic_start = len(getattr(client, "request_diagnostics", []))
        try:
            page = method(item_seq=str(item_seq), page=1, page_size=100)
            if page.total_count > len(page.items):
                results.append(
                    {
                        "source": source,
                        "status": "incomplete",
                        "calls": client.calls - before,
                    }
                )
                continue
            if not page.items:
                results.append(
                    {
                        "source": source,
                        "status": "no_results",
                        "calls": client.calls - before,
                    }
                )
                continue
            # Lists may contain several ingredient rows. Preserve all rather than collapsing them.
            payload = (
                {"_items": page.items} if source == "ingredients" else page.items[0]
            )
            if source != "ingredients" and len(page.items) != 1:
                results.append(
                    {
                        "source": source,
                        "status": "ambiguous",
                        "calls": client.calls - before,
                    }
                )
                continue
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO records VALUES (?,?,?,?)",
                    (
                        source,
                        str(item_seq),
                        json.dumps(payload, ensure_ascii=False),
                        now(),
                    ),
                )
            results.append(
                {"source": source, "status": "ok", "calls": client.calls - before}
            )
        except MFDSError as exc:
            results.append(
                {
                    "source": source,
                    "status": "error",
                    "error": exc.category,
                    "detail": exc.detail,
                    "calls": client.calls - before,
                }
            )
            if exc.category in ("budget_exceeded", "quota", "authentication"):
                break
        finally:
            _save_diagnostics(db, client, source, diagnostic_start)
    return results


def atomic_write(path, write):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".mfds-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            write(f)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def export(db, directory):
    directory = Path(directory)
    counts = {}
    for source in ("permit", "easy", "permit_detail", "ingredients"):
        records = db.execute(
            "SELECT item_seq,payload,retrieved_at FROM records WHERE source=? ORDER BY item_seq",
            (source,),
        ).fetchall()
        cp = db.execute(
            "SELECT next_page,page_size,total,complete,updated_at FROM checkpoints WHERE source=?",
            (source,),
        ).fetchone()
        coverage = (
            {
                "next_page": cp[0],
                "page_size": cp[1],
                "total_reported": cp[2],
                "scan_complete": bool(cp[3]),
                "checkpoint_at": cp[4],
            }
            if cp
            else {"scan_complete": False}
        )
        rows = []
        for seq, payload, retrieved in records:
            raw = json.loads(payload)
            rows.append(
                {
                    "item_seq": seq,
                    "source_api": source,
                    "source_url": SOURCES[
                        "permit" if source == "permit_detail" else source
                    ],
                    "retrieved_at": retrieved,
                    "original_product_name": raw.get("itemName")
                    if source == "easy"
                    else raw.get("ITEM_NAME"),
                    "permit_status": raw.get("CANCEL_NAME")
                    if source in ("permit", "permit_detail")
                    else None,
                    "updated_at": raw.get("updateDe")
                    if source == "easy"
                    else raw.get("CHANGE_DATE"),
                    "collection_status": "collected",
                    "cache_status": "latest_unverified",
                    "raw": raw,
                }
            )
        atomic_write(
            directory / (source + ".json"),
            lambda f: json.dump(
                {"coverage": coverage, "items": rows}, f, ensure_ascii=False, indent=2
            ),
        )

        def write_csv(f):
            fields = [
                "item_seq",
                "source_api",
                "source_url",
                "retrieved_at",
                "original_product_name",
                "permit_status",
                "updated_at",
                "collection_status",
                "cache_status",
                "raw",
            ]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {**row, "raw": json.dumps(row["raw"], ensure_ascii=False)}
                )

        atomic_write(directory / (source + ".csv"), write_csv)
        if source == "permit":
            dictionary = [
                {
                    "item_seq": r["item_seq"],
                    "official_name": r["original_product_name"],
                    "permit_status": r["permit_status"],
                    "retrieved_at": r["retrieved_at"],
                    "source_url": r["source_url"],
                }
                for r in rows
                if r["original_product_name"]
            ]
            atomic_write(
                directory / "drug_name_dictionary.json",
                lambda f: json.dump(
                    {"coverage": coverage, "items": dictionary},
                    f,
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        counts[source] = len(rows)
    return counts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["initial", "update", "export", "item"])
    p.add_argument("--item-seq")
    p.add_argument("--db", default="data/mfds_collection.sqlite3")
    p.add_argument("--output", default="data/mfds_collection")
    p.add_argument("--source", choices=["permit", "easy", "both"], default="both")
    p.add_argument("--max-calls", type=int, default=2)
    p.add_argument("--max-pages", type=int, default=1)
    p.add_argument("--page-size", type=int, default=100)
    args = p.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    db = connect(args.db)
    results = []
    try:
        if args.mode != "export":
            client = MFDSClient(max_calls=args.max_calls, retries=0)
            if args.mode == "item":
                results = collect_item(db, client, args.item_seq)
            else:
                for source in (
                    ("permit", "easy") if args.source == "both" else (args.source,)
                ):
                    results.append(
                        collect(
                            db,
                            client,
                            source,
                            mode=args.mode,
                            max_pages=args.max_pages,
                            page_size=args.page_size,
                        )
                    )
        counts = export(db, args.output)
        print(
            json.dumps({"runs": results, "cached_counts": counts}, ensure_ascii=False)
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
