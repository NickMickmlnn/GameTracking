from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
DEFAULT_DB_PATH = DATA_DIR / "app.db"


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DB_PATH
        self.connection: sqlite3.Connection | None = None

    def init(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"Schema file not found at {SCHEMA_PATH}")

        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        with open(SCHEMA_PATH, "r", encoding="utf-8") as schema_file:
            script = schema_file.read()
        self.connection.executescript(script)
        self.connection.commit()

    @contextmanager
    def get_cursor(self) -> Iterator[sqlite3.Cursor]:
        if self.connection is None:
            raise RuntimeError("Database not initialised")
        cursor = self.connection.cursor()
        try:
            yield cursor
            self.connection.commit()
        finally:
            cursor.close()

    def upsert_game(
        self,
        *,
        igdb_id: int,
        name: str,
        alt_names: Optional[Iterable[str]] = None,
        first_release_year: Optional[int] = None,
    ) -> None:
        alt_names_json = json.dumps(list(alt_names or []))
        now = datetime.utcnow().isoformat()
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO games (igdb_id, name, alt_names_json, first_release_year, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(igdb_id) DO UPDATE SET
                    name=excluded.name,
                    alt_names_json=excluded.alt_names_json,
                    first_release_year=excluded.first_release_year,
                    updated_at=excluded.updated_at
                """,
                (igdb_id, name, alt_names_json, first_release_year, now, now),
            )

    def find_games(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        pattern = f"%{query.lower()}%"
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT igdb_id, name, alt_names_json, first_release_year
                FROM games
                WHERE lower(name) LIKE ? OR lower(alt_names_json) LIKE ?
                ORDER BY name ASC
                LIMIT ?
                """,
                (pattern, pattern, limit),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_game(self, igdb_id: int) -> Optional[Dict[str, Any]]:
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT igdb_id, name, alt_names_json, first_release_year FROM games WHERE igdb_id = ?",
                (igdb_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def get_catalog_by_igdb(self, igdb_id: int, region: str = "US") -> List[Dict[str, Any]]:
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT service, igdb_id, service_title, platforms_json, tier, region, last_seen_at, first_seen_at
                FROM catalog_items
                WHERE igdb_id = ? AND region = ?
                """,
                (igdb_id, region),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def upsert_catalog_item(
        self,
        *,
        service: str,
        igdb_id: int,
        service_title: str,
        platforms: Optional[Iterable[str]],
        tier: Optional[str],
        region: str,
        seen_at: datetime,
    ) -> None:
        platforms_json = json.dumps(list(platforms or []))
        seen_at_iso = seen_at.isoformat()
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT first_seen_at FROM catalog_items WHERE service = ? AND igdb_id = ? AND region = ?",
                (service, igdb_id, region),
            )
            existing = cursor.fetchone()
            first_seen_at = existing[0] if existing else seen_at_iso

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO catalog_items (service, igdb_id, service_title, platforms_json, tier, region, last_seen_at, first_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service, igdb_id, region) DO UPDATE SET
                    service_title=excluded.service_title,
                    platforms_json=excluded.platforms_json,
                    tier=excluded.tier,
                    last_seen_at=excluded.last_seen_at,
                    first_seen_at=?
                """,
                (
                    service,
                    igdb_id,
                    service_title,
                    platforms_json,
                    tier,
                    region,
                    seen_at_iso,
                    first_seen_at,
                    first_seen_at,
                ),
            )

    def cache_igdb_payload(self, name: str, payload: Dict[str, Any]) -> None:
        now = datetime.utcnow().isoformat()
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO igdb_cache (name, igdb_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    igdb_id=excluded.igdb_id,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (name.lower(), payload["igdb_id"], json.dumps(payload), now),
            )

    def get_cached_igdb(self, name: str) -> Optional[Dict[str, Any]]:
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT payload_json FROM igdb_cache WHERE name = ?",
                (name.lower(),),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return json.loads(row[0])


db = Database()
