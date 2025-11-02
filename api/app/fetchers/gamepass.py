from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..database import db
from ..igdb_client import ensure_game_from_catalog, search_games

LOGGER = logging.getLogger(__name__)

APPAGG_BASE_URL = "https://appagg.com/search/@gamepass:xbox/"
DEFAULT_LANGUAGE = "en-us"
MAX_PAGES = 50
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    )
}

_igdb_lookup_cache: Dict[str, Optional[int]] = {}


def _get_market() -> str:
    market = os.getenv("GAMEPASS_MARKET", "US")
    market = market.strip() or "US"
    return market.upper()


def _get_language() -> str:
    language = os.getenv("GAMEPASS_LANGUAGE", DEFAULT_LANGUAGE)
    language = language.strip() or DEFAULT_LANGUAGE
    return language.lower()


def _extract_year(value: str) -> Optional[int]:
    for match in re.finditer(r"(19|20)\d{2}", value or ""):
        try:
            year = int(match.group(0))
        except ValueError:
            continue
        if 1970 <= year <= datetime.utcnow().year:
            return year
    return None


def _extract_platforms_from_text(text: str) -> Set[str]:
    lowered = text.lower()
    platforms: Set[str] = set()
    if any(token in lowered for token in ("windows", "pc", "play anywhere")):
        platforms.add("pc")
    if any(token in lowered for token in ("xbox series", "xbox one", "console")):
        platforms.add("console")
    if any(token in lowered for token in ("cloud", "xcloud")):
        platforms.add("cloud")
    return platforms


def _extract_product_id(url: str, candidate_text: str = "") -> str:
    parsed = urlparse(url)
    slug = parsed.path.rstrip("/").split("/")[-1]
    if slug:
        return slug
    cleaned = re.sub(r"[^A-Za-z0-9]", "", candidate_text)
    return cleaned or url


def _iter_platform_text_sources(node: Optional[BeautifulSoup]) -> Iterable[str]:
    if not node:
        return []
    selectors = [
        "[class*=platform]",
        "[class*=tag]",
        "[class*=label]",
        "[class*=badge]",
        "[class*=note]",
    ]
    for selector in selectors:
        for child in node.select(selector):
            text = child.get_text(" ", strip=True)
            if text:
                yield text


def _find_card_node(anchor) -> Optional[BeautifulSoup]:
    for parent in anchor.parents:
        if parent.name in {"article", "li", "div"}:
            classes = " ".join(parent.get("class", []))
            if parent.has_attr("data-app-id") or "app" in classes or "card" in classes:
                return parent
    return anchor.parent if anchor else None


def _parse_appagg_page(markup: str) -> tuple[List[dict], bool]:
    soup = BeautifulSoup(markup, "html.parser")
    entries: Dict[str, dict] = {}

    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not href:
            continue
        full_url = urljoin(APPAGG_BASE_URL, href)
        if "appagg.com" in urlparse(full_url).netloc and "@gamepass:xbox" in full_url:
            # Skip self-links and filters.
            continue
        if not any(host in full_url for host in ("microsoft.com", "apps.microsoft.com")):
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue

        card = _find_card_node(anchor)
        card_text = card.get_text(" ", strip=True) if card else anchor.get_text(" ", strip=True)
        release_year = _extract_year(card_text)
        product_id = _extract_product_id(full_url, candidate_text=title)

        platforms: Set[str] = set()
        for text in _iter_platform_text_sources(card):
            platforms.update(_extract_platforms_from_text(text))
        platforms.update(_extract_platforms_from_text(card_text))

        entry = entries.setdefault(
            product_id,
            {
                "title": title,
                "platforms": set(),
                "release_year": release_year,
            },
        )
        entry["platforms"].update(platforms)
        if release_year and not entry.get("release_year"):
            entry["release_year"] = release_year

    has_next = bool(
        soup.select_one("a[rel='next']")
        or soup.select_one(".pagination__item--next a")
        or soup.select_one("[aria-label='Next']")
    )

    results = []
    for product_id, info in entries.items():
        results.append(
            {
                "id": product_id,
                "title": info["title"],
                "platforms": sorted(info["platforms"]),
                "release_year": info.get("release_year"),
            }
        )
    return results, has_next


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

    inserted = 0
    params = {"hl": language}
    page = 1

    with httpx.Client(headers=REQUEST_HEADERS, follow_redirects=True) as client:
        while page <= MAX_PAGES:
            request_params = dict(params)
            if page > 1:
                request_params["page"] = page
            try:
                response = client.get(APPAGG_BASE_URL, params=request_params, timeout=30)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                LOGGER.warning("Unable to download Game Pass page %s: %s", page, exc)
                break

            entries, has_next = _parse_appagg_page(html.unescape(response.text))
            if not entries and page == 1:
                LOGGER.warning("No Game Pass entries detected on the first AppAgg page")
                break

            for entry in entries:
                title = entry["title"]
                igdb_id = _resolve_igdb_id(title)
                if not igdb_id:
                    LOGGER.debug("Skipping Game Pass entry without IGDB match: %s", title)
                    continue

                ensure_game_from_catalog(
                    title,
                    igdb_id,
                    first_release_year=entry.get("release_year"),
                )
                try:
                    db.upsert_catalog_item(
                        service="gamepass",
                        igdb_id=igdb_id,
                        service_title=title,
                        platforms=entry.get("platforms") or [],
                        tier=None,
                        region=region,
                        seen_at=now,
                    )
                    inserted += 1
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Failed to upsert Game Pass entry for %s: %s", title, exc)

            if not has_next:
                break
            page += 1

    LOGGER.info("Refreshed Game Pass catalogue with %s entries", inserted)
    return inserted
