from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List

from ..database import db
from ..igdb_client import ensure_game_from_catalog

MOCK_DATA_PATH = Path(__file__).with_suffix(".json")


def refresh_mock_gamepass(region: str = "US") -> int:
    if not MOCK_DATA_PATH.exists():
        raise FileNotFoundError(f"Mock Game Pass data not found at {MOCK_DATA_PATH}")

    raw_data: List[dict] = json.loads(MOCK_DATA_PATH.read_text(encoding="utf-8"))
    now = datetime.utcnow()
    inserted = 0
    for entry in raw_data:
        igdb_id = int(entry["igdb_id"])
        ensure_game_from_catalog(
            entry["name"],
            igdb_id,
            first_release_year=entry.get("first_release_year"),
        )
        db.upsert_catalog_item(
            service="gamepass",
            igdb_id=igdb_id,
            service_title=entry["service_title"],
            platforms=entry.get("platforms", []),
            tier=None,
            region=region,
            seen_at=now,
        )
        inserted += 1
    return inserted
