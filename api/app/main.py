from __future__ import annotations

import json
import logging
import os
from typing import Dict, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import db
from .fetchers.mock_gamepass import refresh_mock_gamepass
from .igdb_client import search_games

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="Game Availability API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


PLATFORM_LABELS = {
    "console": "Console",
    "pc": "PC",
    "cloud": "Cloud",
}
PLATFORM_ORDER = ["console", "pc", "cloud"]


def _normalise_platforms(raw: object) -> List[str]:
    tokens: List[str] = []
    if isinstance(raw, str) and raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                tokens = [str(item).strip().lower() for item in loaded if isinstance(item, (str, int))]
        except json.JSONDecodeError:
            tokens = []
    elif isinstance(raw, list):
        tokens = [str(item).strip().lower() for item in raw if isinstance(item, (str, int))]

    tokens = [token for token in tokens if token]
    if not tokens:
        return []

    ordered: List[str] = []
    seen = set()
    for key in PLATFORM_ORDER:
        if key in tokens:
            ordered.append(key)
            seen.add(key)
    for token in tokens:
        if token not in seen:
            ordered.append(token)
            seen.add(token)
    return ordered


def _platform_labels(tokens: List[str]) -> List[str]:
    if not tokens:
        return []
    labels: List[str] = []
    for token in tokens:
        label = PLATFORM_LABELS.get(token)
        if not label:
            label = token.replace("_", " ").title()
        labels.append(label)
    return labels


def _summarise_service(rows: List[Dict[str, object]], service: str) -> Dict[str, object]:
    service_rows = [row for row in rows if row["service"] == service]
    if not service_rows:
        return {"available": False}

    latest = max(service_rows, key=lambda row: row.get("last_seen_at", ""))
    platforms = _normalise_platforms(latest.get("platforms_json"))
    platform_labels = _platform_labels(platforms)

    summary: Dict[str, object] = {
        "available": True,
        "service_title": latest.get("service_title"),
        "platforms": platforms,
        "platform_labels": platform_labels,
        "first_seen_at": latest.get("first_seen_at"),
        "last_seen_at": latest.get("last_seen_at"),
    }
    if service == "psplus" and latest.get("tier"):
        summary["tier"] = latest["tier"]
    return summary


def _mock_data_enabled() -> bool:
    flag = os.getenv("ENABLE_MOCK_DATA", "true").strip().lower()
    return flag not in {"0", "false", "no", "off"}


@app.on_event("startup")
def startup() -> None:
    db.init()
    if _mock_data_enabled():
        inserted = refresh_mock_gamepass()
        LOGGER.info("Loaded %s mock Game Pass entries", inserted)
    else:
        LOGGER.info("Mock catalog seeding disabled; starting with empty catalog")


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/search")
def search(q: str) -> Dict[str, object]:
    candidates = search_games(q)
    results = []
    for candidate in candidates:
        igdb_id = int(candidate["igdb_id"])
        catalog_rows = db.get_catalog_by_igdb(igdb_id)
        results.append(
            {
                "name": candidate["name"],
                "igdb_id": igdb_id,
                "first_release_year": candidate.get("first_release_year"),
                "services": {
                    "gamepass": _summarise_service(catalog_rows, "gamepass"),
                    "psplus": _summarise_service(catalog_rows, "psplus"),
                    "ubisoftplus": _summarise_service(catalog_rows, "ubisoftplus"),
                },
            }
        )
    return {"query": q, "results": results}


@app.post("/refresh")
def refresh() -> Dict[str, object]:
    if not _mock_data_enabled():
        return {"status": "ok", "counts": {}}

    inserted = refresh_mock_gamepass()
    return {"status": "ok", "counts": {"gamepass": inserted}}
