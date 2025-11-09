from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Dict, List, Optional, Set

import httpx

from ..database import db
from ..igdb_client import ensure_game_from_catalog, search_games

LOGGER = logging.getLogger(__name__)

DEFAULT_API_BASE_URL = "https://gamepass-api.com/api/v1/games"
DEFAULT_TIMEOUT = 30
_igdb_lookup_cache: Dict[str, Optional[int]] = {}


def _get_market() -> str:
    market = os.getenv("GAMEPASS_MARKET", "US")
    market = market.strip() or "US"
    return market.upper()


def _get_api_base_url() -> str:
    url = os.getenv("GAMEPASS_API_BASE_URL", DEFAULT_API_BASE_URL).strip()
    return url or DEFAULT_API_BASE_URL


def _get_timeout() -> float:
    raw = os.getenv("GAMEPASS_API_TIMEOUT")
    if not raw:
        return float(DEFAULT_TIMEOUT)
    try:
        return max(5.0, float(raw))
    except ValueError:
        return float(DEFAULT_TIMEOUT)


def _extract_year(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    for match in re.finditer(r"(19|20)\d{2}", value):
        try:
            year = int(match.group(0))
        except ValueError:
            continue
        if 1970 <= year <= datetime.utcnow().year:
            return year
    return None


def _normalise_platform_token(token: str) -> Optional[str]:
    lowered = token.strip().lower()
    if not lowered:
        return None
    mapping = {
        "pc": "pc",
        "windows": "pc",
        "win": "pc",
        "pc game pass": "pc",
        "xbox": "console",
        "console": "console",
        "xbox console": "console",
        "xbox game pass": "console",
        "xbox one": "console",
        "xbox series": "console",
        "cloud": "cloud",
        "cloud gaming": "cloud",
        "xcloud": "cloud",
    }
    if lowered in mapping:
        return mapping[lowered]
    if "cloud" in lowered:
        return "cloud"
    if "pc" in lowered or "windows" in lowered:
        return "pc"
    if "xbox" in lowered or "console" in lowered:
        return "console"
    return lowered


def _extract_platforms(entry: Dict[str, object]) -> List[str]:
    platforms: Set[str] = set()

    platform_lists = [
        entry.get("platforms"),
        entry.get("availableOn"),
        entry.get("availability"),
    ]
    for platform_list in platform_lists:
        if isinstance(platform_list, str):
            platform_list = [platform_list]
        if isinstance(platform_list, Iterable):
            for item in platform_list:
                if isinstance(item, str):
                    normalised = _normalise_platform_token(item)
                    if normalised:
                        platforms.add(normalised)

    bool_mappings = {
        "isConsole": "console",
        "isXbox": "console",
        "isPc": "pc",
        "isPC": "pc",
        "isCloud": "cloud",
        "cloudEnabled": "cloud",
        "console": "console",
        "pc": "pc",
        "cloud": "cloud",
    }
    for key, platform in bool_mappings.items():
        value = entry.get(key)
        if isinstance(value, bool) and value:
            platforms.add(platform)
        elif isinstance(value, str) and value.lower() in {"true", "yes", "1"}:
            platforms.add(platform)

    if not platforms:
        platform_text = entry.get("platformNotes") or entry.get("notes")
        if isinstance(platform_text, str):
            normalised = _normalise_platform_token(platform_text)
            if normalised:
                platforms.add(normalised)

    ordered = []
    for token in ("console", "pc", "cloud"):
        if token in platforms:
            ordered.append(token)
    for token in sorted(platforms):
        if token not in ordered:
            ordered.append(token)
    return ordered


def _iter_api_entries(client: httpx.Client, base_url: str) -> Iterable[Dict[str, object]]:
    next_url = base_url
    params: Dict[str, object] = {}
    seen_ids: Set[str] = set()

    while next_url:
        try:
            response = client.get(next_url, params=params, timeout=_get_timeout())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            LOGGER.warning("Failed to download Game Pass data from %s: %s", next_url, exc)
            break

        try:
            payload = response.json()
        except ValueError:
            LOGGER.warning("Game Pass API returned non-JSON payload from %s", next_url)
            break

        if isinstance(payload, list):
            entries = payload
            next_url = None
        elif isinstance(payload, dict):
            entries = (
                payload.get("results")
                or payload.get("data")
                or payload.get("games")
                or payload.get("items")
                or []
            )
            next_value = payload.get("next")
            continuation = (
                payload.get("continuationToken")
                or payload.get("nextPageToken")
                or payload.get("skiptoken")
            )
            if isinstance(next_value, dict):
                continuation = continuation or next_value.get("token")
                next_value = next_value.get("href") or next_value.get("url")

            if isinstance(next_value, str) and next_value:
                if next_value.startswith("http"):
                    next_url = next_value
                    params = {}
                else:
                    next_url = base_url
                    params = {"next": next_value}
            elif isinstance(continuation, str) and continuation:
                next_url = base_url
                params = {"continuationToken": continuation}
            else:
                next_url = None
        else:
            LOGGER.warning("Unexpected Game Pass API payload type: %s", type(payload))
            break

        if not isinstance(entries, list):
            LOGGER.warning("Unexpected entries type from Game Pass API: %s", type(entries))
            break

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identifier = str(
                entry.get("gamePassId")
                or entry.get("productId")
                or entry.get("id")
                or entry.get("titleId")
                or entry.get("slug")
                or entry.get("name")
            )
            if not identifier:
                continue
            if identifier in seen_ids:
                continue
            seen_ids.add(identifier)
            yield entry

        if isinstance(payload, list):
            next_url = None


def _resolve_igdb_id(title: str) -> Optional[int]:
    cached_id = _igdb_lookup_cache.get(title.lower())
    if cached_id is not None:
        return cached_id

    cached = db.get_cached_igdb(title)
    if cached:
        try:
            igdb_id = int(cached["igdb_id"])
            _igdb_lookup_cache[title.lower()] = igdb_id
            return igdb_id
        except (KeyError, TypeError, ValueError):
            pass
    try:
        results = search_games(title, limit=1)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("IGDB search failed for %s: %s", title, exc)
        _igdb_lookup_cache[title.lower()] = None
        return None
    if not results:
        _igdb_lookup_cache[title.lower()] = None
        return None
    try:
        igdb_id = int(results[0]["igdb_id"])
        _igdb_lookup_cache[title.lower()] = igdb_id
        return igdb_id
    except (KeyError, TypeError, ValueError):
        _igdb_lookup_cache[title.lower()] = None
        return None


def refresh_gamepass_us(region: Optional[str] = None) -> int:
    market = (region or _get_market()).upper()
    region = region or market
    now = datetime.utcnow()

    inserted = 0
    base_url = _get_api_base_url()

    headers = {
        "User-Agent": (
            "GameTracking/0.1 (+https://github.com/NikkelM/Game-Pass-API integration)"
        )
    }

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for entry in _iter_api_entries(client, base_url):
            title = entry.get("title") or entry.get("name")
            if not isinstance(title, str) or not title.strip():
                continue

            igdb_id: Optional[int] = None
            for key in ("igdbId", "igdb_id", "igdb"):
                value = entry.get(key)
                if value is None:
                    continue
                try:
                    igdb_id = int(value)
                    break
                except (TypeError, ValueError):
                    continue

            if not igdb_id:
                igdb_id = _resolve_igdb_id(title)
            if not igdb_id:
                LOGGER.debug("Skipping Game Pass entry without IGDB match: %s", title)
                continue

            release_year: Optional[int] = None
            for key in ("releaseDate", "release_date", "firstReleaseDate", "first_release_date"):
                value = entry.get(key)
                if isinstance(value, (int, float)):
                    candidate_year = int(value)
                    if 1970 <= candidate_year <= datetime.utcnow().year:
                        release_year = candidate_year
                        break
                elif isinstance(value, str):
                    year = _extract_year(value)
                    if year:
                        release_year = year
                        break

            platforms = _extract_platforms(entry)

            ensure_game_from_catalog(
                title,
                igdb_id,
                first_release_year=release_year,
            )
            try:
                db.upsert_catalog_item(
                    service="gamepass",
                    igdb_id=igdb_id,
                    service_title=title,
                    platforms=platforms,
                    tier=None,
                    region=region,
                    seen_at=now,
                )
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to upsert Game Pass entry for %s: %s", title, exc)

    LOGGER.info("Refreshed Game Pass catalogue with %s entries", inserted)
    return inserted
