from __future__ import annotations

import json
import logging
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


def _summarise_service(rows: List[Dict[str, object]], service: str) -> Dict[str, object]:
    service_rows = [row for row in rows if row["service"] == service]
    if not service_rows:
        return {"available": False}

    latest = max(service_rows, key=lambda row: row.get("last_seen_at", ""))
    platforms: List[str] = []
    try:
        platforms_raw = latest.get("platforms_json")
        if platforms_raw:
            platforms = json.loads(platforms_raw)
    except json.JSONDecodeError:
        platforms = []

    summary: Dict[str, object] = {
        "available": True,
        "service_title": latest.get("service_title"),
        "platforms": platforms,
        "first_seen_at": latest.get("first_seen_at"),
        "last_seen_at": latest.get("last_seen_at"),
    }
    if service == "psplus" and latest.get("tier"):
        summary["tier"] = latest["tier"]
    return summary


@app.on_event("startup")
def startup() -> None:
    db.init()
    inserted = refresh_mock_gamepass()
    LOGGER.info("Loaded %s mock Game Pass entries", inserted)


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
    inserted = refresh_mock_gamepass()
    return {"status": "ok", "counts": {"gamepass": inserted}}
