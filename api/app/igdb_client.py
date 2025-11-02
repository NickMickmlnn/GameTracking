from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx

from .database import db

LOGGER = logging.getLogger(__name__)
IGDB_BASE_URL = "https://api.igdb.com/v4"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

_token_cache: Dict[str, Optional[str | datetime]] = {"value": None, "expires": None}


def _get_credentials() -> tuple[str, str] | tuple[None, None]:
    client_id = os.getenv("TWITCH_CLIENT_ID")
    client_secret = os.getenv("TWITCH_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None, None
    return client_id, client_secret


def _request_token(client_id: str, client_secret: str) -> tuple[str, int]:
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"], int(payload.get("expires_in", 0))


def _get_token() -> Optional[str]:
    client_id, client_secret = _get_credentials()
    if not client_id or not client_secret:
        return None

    now = datetime.utcnow()
    token = _token_cache.get("value")
    expires = _token_cache.get("expires")
    if token and isinstance(expires, datetime) and expires > now:
        return token

    access_token, expires_in = _request_token(client_id, client_secret)
    _token_cache["value"] = access_token
    _token_cache["expires"] = now + timedelta(seconds=max(expires_in - 60, 0))
    return access_token


def _remote_search(query: str, limit: int) -> List[Dict[str, object]]:
    token = _get_token()
    if not token:
        raise RuntimeError("IGDB credentials are not configured")

    client_id, _ = _get_credentials()
    assert client_id is not None

    body = (
        f"search \"{query}\";"
        " fields name,alternative_names.name,first_release_date;"
        f" limit {limit};"
    )
    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }
    response = httpx.post(
        f"{IGDB_BASE_URL}/games",
        data=body,
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    results: List[Dict[str, object]] = []
    for game in response.json():
        alt_names = [alt.get("name") for alt in game.get("alternative_names", []) if alt.get("name")]
        first_release_date = game.get("first_release_date")
        first_release_year = None
        if first_release_date:
            first_release_year = datetime.utcfromtimestamp(first_release_date).year
        record = {
            "igdb_id": game["id"],
            "name": game.get("name", ""),
            "alt_names": alt_names,
            "first_release_year": first_release_year,
        }
        results.append(record)
    return results


def _serialise_cached(row: Dict[str, object]) -> Dict[str, object]:
    alt_names: List[str] = []
    alt_json = row.get("alt_names_json")
    if isinstance(alt_json, str) and alt_json:
        try:
            alt_names = json.loads(alt_json)
        except json.JSONDecodeError:
            alt_names = []
    return {
        "igdb_id": row["igdb_id"],
        "name": row["name"],
        "alt_names": alt_names,
        "first_release_year": row.get("first_release_year"),
    }


def _fallback_search(query: str, limit: int) -> List[Dict[str, object]]:
    try:
        rows = db.find_games(query, limit=limit)
    except RuntimeError:
        return []
    return [_serialise_cached(row) for row in rows]


def search_games(query: str, limit: int = 5) -> List[Dict[str, object]]:
    query = query.strip()
    if not query:
        return []

    try:
        remote_results = _remote_search(query, limit)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Falling back to cached IGDB data: %s", exc)
        remote_results = []

    results: List[Dict[str, object]] = []
    if remote_results:
        for record in remote_results:
            try:
                db.upsert_game(
                    igdb_id=int(record["igdb_id"]),
                    name=str(record["name"]),
                    alt_names=record.get("alt_names") or [],
                    first_release_year=record.get("first_release_year"),
                )
                db.cache_igdb_payload(record["name"], record)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Unable to cache IGDB record %s: %s", record["igdb_id"], exc)
            results.append(record)

    if not results:
        results = _fallback_search(query, limit)
    return results[:limit]


def ensure_game_from_catalog(name: str, igdb_id: int, *, first_release_year: Optional[int] = None) -> None:
    try:
        existing = db.get_game(igdb_id)
    except RuntimeError:
        return

    if existing:
        return

    db.upsert_game(igdb_id=igdb_id, name=name, alt_names=[name], first_release_year=first_release_year)
