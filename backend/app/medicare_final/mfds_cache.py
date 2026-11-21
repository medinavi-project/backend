"""Read-only access to explicitly collected official records; no network on import."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def policy():
    def flag(name, default):
        return os.getenv(name, default).lower() in ("1", "true", "yes")

    try:
        hours = float(os.getenv("MFDS_CACHE_MAX_AGE_HOURS", "24"))
        if not 0 <= hours <= 87600:
            raise ValueError()
    except ValueError:
        hours = 0
    return {
        "enabled": flag("MFDS_CACHE_ENABLED", "false"),
        "max_age_hours": hours,
        "api_first": flag("MFDS_CACHE_API_FIRST", "true"),
        "return_stale": flag("MFDS_CACHE_RETURN_STALE", "true"),
        "force_refresh": flag("MFDS_CACHE_FORCE_REFRESH", "false"),
        "path": os.getenv("MFDS_CACHE_DB", "data/mfds_collection.sqlite3"),
    }


def read_record(seq, origin, config):
    mapping = {
        "permit_list": "permit",
        "permit": "permit_detail",
        "easy": "easy",
        "ingredients": "ingredients",
    }
    path = Path(config["path"]).resolve()
    if origin not in mapping or not path.is_file():
        return None
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            row = db.execute(
                "SELECT payload,retrieved_at FROM records WHERE source=? AND item_seq=?",
                (mapping[origin], seq),
            ).fetchone()
        finally:
            db.close()
        if not row:
            return None
        payload = json.loads(row[0])
        stamp = datetime.fromisoformat(row[1])
        if stamp.tzinfo is None:
            return None
        age = (
            datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
        ).total_seconds()
        if age < 0:
            return None
        items = payload.get("_items") if origin == "ingredients" else [payload]
        field = "itemSeq" if origin == "easy" else "ITEM_SEQ"
        if (
            not isinstance(items, list)
            or not items
            or any(
                not isinstance(x, dict) or str(x.get(field, "")).strip() != seq
                for x in items
            )
        ):
            return None
        if origin != "ingredients" and len(items) != 1:
            return None
        return {
            "status": "ok",
            "items": items,
            "total_count": len(items),
            "retrieved_at": row[1],
            "cache_status": "fresh"
            if age <= config["max_age_hours"] * 3600
            else "stale",
        }
    except (OSError, ValueError, TypeError, sqlite3.Error, AttributeError):
        return None
