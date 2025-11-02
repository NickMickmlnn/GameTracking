PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    igdb_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    alt_names_json TEXT DEFAULT '[]',
    first_release_year INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS catalog_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL CHECK(service IN ('gamepass', 'psplus', 'ubisoftplus')),
    igdb_id INTEGER NOT NULL,
    service_title TEXT NOT NULL,
    platforms_json TEXT NOT NULL DEFAULT '[]',
    tier TEXT,
    region TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    FOREIGN KEY (igdb_id) REFERENCES games(igdb_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_unique
    ON catalog_items(service, igdb_id, region);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS igdb_cache (
    name TEXT PRIMARY KEY,
    igdb_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
