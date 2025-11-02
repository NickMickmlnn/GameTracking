from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

import httpx

from ..database import db
from ..igdb_client import ensure_game_from_catalog, search_games

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://catalog.gamepass.com/sigls/v2"
# The primary feed contains the cross-service catalogue; the EA Play feed is
# appended so the PC catalogue matches what Ultimate subscribers see.
GAMEPASS_FEED_IDS: Tuple[str, ...] = (
    "7d2390a1-7554-4a67-9b26-1ce1bde0ad24",  # Game Pass catalogue (console/pc/cloud)
    "eaec3f88-887d-4f9c-b2d9-919163f8f196",  # EA Play catalogue bundled with Game Pass
)
DEFAULT_LANGUAGE = "en-us"

_igdb_lookup_cache: Dict[str, Optional[int]] = {}


def _get_market() -> str:
    market = os.getenv("GAMEPASS_MARKET", "US")
    market = market.strip() or "US"
    return market.upper()


def _get_language() -> str:
    language = os.getenv("GAMEPASS_LANGUAGE", DEFAULT_LANGUAGE)
    language = language.strip() or DEFAULT_LANGUAGE
    return language.lower()


def _fetch_feed(client: httpx.Client, feed_id: str, market: str, language: str) -> List[dict]:
    response = client.get(
        BASE_URL,
        params={"id": feed_id, "market": market, "language": language},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        # Some feeds return an object with a "Products" root instead of an array of clusters.
        payload = [payload]
    return payload


def _extract_title(product: dict) -> Optional[str]:
    localized = product.get("LocalizedProperties") or []
    for item in localized:
        title = item.get("ProductTitle") or item.get("Title")
        if title:
            return str(title)
    title = product.get("ProductTitle") or product.get("Title")
    if title:
        return str(title)
    return None


def _extract_release_year(product: dict) -> Optional[int]:
    properties = product.get("Properties") or {}
    release_date = properties.get("OriginalReleaseDate") or properties.get("ReleaseDate")
    if not release_date:
        localized = product.get("LocalizedProperties") or []
        for item in localized:
            release_date = item.get("ReleaseDate")
            if release_date:
                break
    if not release_date:
        return None
    try:
        # Date strings are ISO 8601; only the year is required for display purposes.
        return datetime.fromisoformat(str(release_date).replace("Z", "+00:00")).year
    except ValueError:
        return None


def _extract_platforms(product: dict) -> Set[str]:
    platforms: Set[str] = set()
    tags = {str(tag).lower() for tag in product.get("Tags") or []}
    if "gamepasspc" in tags or "pc" in tags:
        platforms.add("pc")
    if "gamepassconsole" in tags or "console" in tags or "xbox" in tags:
        platforms.add("console")
    if "gamepasscloud" in tags or "cloud" in tags or "xcloud" in tags:
        platforms.add("cloud")

    for availability in product.get("DisplaySkuAvailabilities") or []:
        sku = availability.get("Sku") or {}
        props = sku.get("Properties") or {}
        supported = props.get("SupportedPlatforms") or props.get("Platforms")
        if isinstance(supported, Iterable) and not isinstance(supported, (str, bytes)):
            for platform in supported:
                token = str(platform).lower()
                if "xbox" in token:
                    platforms.add("console")
                if any(key in token for key in ("windows", "pc")):
                    platforms.add("pc")
        for attribute in props.get("Attributes") or []:
            name = str(attribute.get("Name", "")).lower()
            value = str(attribute.get("Value", "")).lower()
            if "cloud" in name or "cloud" in value:
                platforms.add("cloud")
            if "xbox" in name or "xbox" in value:
                platforms.add("console")
            if "pc" in name or "windows" in value:
                platforms.add("pc")
    return platforms


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
    language = _get_language()
    now = datetime.utcnow()

    with httpx.Client() as client:
        products: Dict[str, dict] = {}
        for feed_id in GAMEPASS_FEED_IDS:
            try:
                clusters = _fetch_feed(client, feed_id, market, language)
            except httpx.HTTPError as exc:
                LOGGER.warning("Unable to download Game Pass feed %s: %s", feed_id, exc)
                continue
            for cluster in clusters:
                for product in cluster.get("Products") or []:
                    product_id = product.get("ProductId") or product.get("id")
                    if not product_id:
                        continue
                    existing = products.get(product_id)
                    if existing:
                        # Merge tag and SKU data so platform inference improves when
                        # a product appears in multiple feeds.
                        existing_tags = set(existing.get("Tags") or [])
                        new_tags = set(product.get("Tags") or [])
                        product["Tags"] = list(existing_tags | new_tags)
                        existing_skus = existing.get("DisplaySkuAvailabilities") or []
                        new_skus = product.get("DisplaySkuAvailabilities") or []
                        product["DisplaySkuAvailabilities"] = existing_skus + new_skus
                    products[product_id] = product

    inserted = 0
    for product in products.values():
        title = _extract_title(product)
        if not title:
            continue
        release_year = _extract_release_year(product)
        platforms = sorted(_extract_platforms(product))

        igdb_id = _resolve_igdb_id(title)
        if not igdb_id:
            LOGGER.debug("Skipping Game Pass entry without IGDB match: %s", title)
            continue

        ensure_game_from_catalog(title, igdb_id, first_release_year=release_year)
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
