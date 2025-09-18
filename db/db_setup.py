# schema.py
# Usage:
#   python schema.py gifs.db
#
# Erstellt eine SQLite-DB mit normalisierter Struktur:
# gifs (id, title, url, nsfw, anime, created_at)
# characters (id, name), tags (id, name)
# gif_characters (gif_id, character_id), gif_tags (gif_id, tag_id)

import sqlite3
import sys
from pathlib import Path

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS gifs (
    id          INTEGER PRIMARY KEY,                 -- erlaubt eigene IDs ODER AUTOINCREMENT
    title       TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,                -- Links sollen eindeutig sein
    nsfw        INTEGER NOT NULL CHECK (nsfw IN (0,1)),
    anime       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) -- UTC ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_gifs_title       ON gifs(title);
CREATE INDEX IF NOT EXISTS idx_gifs_anime       ON gifs(anime);
CREATE INDEX IF NOT EXISTS idx_gifs_nsfw        ON gifs(nsfw);
CREATE INDEX IF NOT EXISTS idx_gifs_created_at  ON gifs(created_at);

CREATE TABLE IF NOT EXISTS characters (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS gif_characters (
    gif_id        INTEGER NOT NULL,
    character_id  INTEGER NOT NULL,
    PRIMARY KEY (gif_id, character_id),
    FOREIGN KEY (gif_id)       REFERENCES gifs(id)        ON DELETE CASCADE,
    FOREIGN KEY (character_id) REFERENCES characters(id)  ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS gif_tags (
    gif_id  INTEGER NOT NULL,
    tag_id  INTEGER NOT NULL,
    PRIMARY KEY (gif_id, tag_id),
    FOREIGN KEY (gif_id) REFERENCES gifs(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_characters_name ON characters(name);
CREATE INDEX IF NOT EXISTS idx_tags_name       ON tags(name);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at  TEXT                 -- optionales Ablaufdatum, NULL = nie
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


def create_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(DDL)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/db/gifs.db"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    create_db(path)
    print(f"SQLite schema initialized at: {path}")
