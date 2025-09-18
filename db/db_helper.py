# db_helper.py
# High-level Helper für Inserts/Updates/Deletes.
#
# Beispiele:
#   from db_helper import GifDB
#   db = GifDB("gifs.db")
#   gif_id = db.insert_gif(
#       title="NazunaSmoke",
#       url="https://64.media.tumblr.com/...gif",
#       nsfw=False,
#       anime="Call of the Night",
#       characters=["Nazuna"],
#       tags=["smoking", "night"],
#       id=1  # optional; weglassen => auto id
#   )
#   db.update_gif(gif_id, title="Nazuna Smoking", tags=["smoking", "night", "sfw"])
#   db.delete_gif(gif_id)

import sqlite3
from typing import Iterable, Optional, Dict, Any, List
import secrets
from datetime import datetime, timedelta, timezone
import difflib


class GifDB:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # ---------- Internal helpers ----------

    def _get_or_create(self, conn: sqlite3.Connection, table: str, name: str) -> int:
        name = name.strip()
        # Für Tags empfehle ich Lowercase-Kanonisierung – hier bewusst NICHT forciert,
        # damit du selbst entscheiden kannst. Optional:
        # if table == "tags": name = name.lower()
        cur = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute(f"INSERT INTO {table}(name) VALUES (?)", (name,))
        return cur.lastrowid

    def _replace_links(
        self,
        conn: sqlite3.Connection,
        link_table: str,
        main_id_col: str,
        ref_id_col: str,
        gif_id: int,
        names: Iterable[str],
        ref_table: str,
    ) -> None:
        # Ersetzt alle Zuordnungen vollständig (idempotent).
        conn.execute(f"DELETE FROM {link_table} WHERE {main_id_col} = ?", (gif_id,))
        ids = []
        for n in names or []:
            if not n or not str(n).strip():
                continue
            rid = self._get_or_create(conn, ref_table, str(n))
            ids.append(rid)
        conn.executemany(
            f"INSERT OR IGNORE INTO {link_table}({main_id_col}, {ref_id_col}) VALUES (?,?)",
            [(gif_id, rid) for rid in ids],
        )

    # ---------- Public API ----------

    def insert_gif(
        self,
        *,
        title: str,
        url: str,
        nsfw: bool,
        anime: Optional[str] = None,
        characters: Optional[Iterable[str]] = None,
        tags: Optional[Iterable[str]] = None,
        created_at: Optional[str] = None,  # ISO-8601; sonst DEFAULT now()
    ) -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            fields = ["title", "url", "nsfw", "anime"]
            vals: List[Any] = [title, url, 1 if nsfw else 0, anime]

            if created_at is not None:
                fields.append("created_at")
                vals.append(created_at)

            placeholders = ",".join("?" for _ in fields)
            sql = f"INSERT INTO gifs({','.join(fields)}) VALUES ({placeholders})"
            cur.execute(sql, tuple(vals))
            gif_id = cur.lastrowid

            self._replace_links(
                conn,
                "gif_characters",
                "gif_id",
                "character_id",
                gif_id,
                characters,
                "characters",
            )
            self._replace_links(
                conn, "gif_tags", "gif_id", "tag_id", gif_id, tags, "tags"
            )

            return gif_id

    def update_gif(
        self,
        gif_id: int,
        *,
        title: Optional[str] = None,
        url: Optional[str] = None,
        nsfw: Optional[bool] = None,
        anime: Optional[str] = None,
        characters: Optional[Iterable[str]] = None,
        tags: Optional[Iterable[str]] = None,
    ) -> None:
        """
        Aktualisiert Felder selektiv. Wenn 'characters' oder 'tags' übergeben werden,
        wird die Zuordnung komplett ersetzt. Übergib [] um alle zu entfernen.
        """
        with self._connect() as conn:
            # Update primitives
            sets = []
            vals: List[Any] = []
            if title is not None:
                sets.append("title = ?")
                vals.append(title)
            if url is not None:
                sets.append("url = ?")
                vals.append(url)
            if nsfw is not None:
                sets.append("nsfw = ?")
                vals.append(1 if nsfw else 0)
            if anime is not None:
                sets.append("anime = ?")
                vals.append(anime)

            if sets:
                vals.append(gif_id)
                conn.execute(
                    f"UPDATE gifs SET {', '.join(sets)} WHERE id = ?", tuple(vals)
                )

            # Replace relations if provided
            if characters is not None:
                self._replace_links(
                    conn,
                    "gif_characters",
                    "gif_id",
                    "character_id",
                    gif_id,
                    characters,
                    "characters",
                )
            if tags is not None:
                self._replace_links(
                    conn, "gif_tags", "gif_id", "tag_id", gif_id, tags, "tags"
                )

    def delete_gif(self, gif_id: int) -> None:
        """
        Löscht das GIF und (per ON DELETE CASCADE) die Zuordnungen.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM gifs WHERE id = ?", (gif_id,))

    # ---------- Optional nützliche Utilities ----------

    def insert_from_json_obj(self, obj: Dict[str, Any]) -> int:
        """
        Erwartet ein einzelnes Objekt entsprechend deinem JSON-Schema.
        Beispiel (KOMMA nach nsfw nicht vergessen!):
        {
          "id": 1,
          "title": "NazunaSmoke",
          "url": "https://...",
          "nsfw": false,
          "anime": "Call of the Night",
          "characters": ["Nazuna"],
          "tags": ["smoking", "night"]
        }
        """
        return self.insert_gif(
            title=obj["title"],
            url=obj["url"],
            nsfw=bool(obj["nsfw"]),
            anime=obj.get("anime"),
            characters=obj.get("characters") or [],
            tags=obj.get("tags") or [],
        )

    def _compose_gif(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> Dict[str, Any]:
        gif_id = row["id"]
        chars = [
            r["name"]
            for r in conn.execute(
                """
                SELECT c.name
                FROM characters c
                JOIN gif_characters gc ON gc.character_id = c.id
                WHERE gc.gif_id = ?
                ORDER BY c.name
                """,
                (gif_id,),
            )
        ]
        tags = [
            r["name"]
            for r in conn.execute(
                """
                SELECT t.name
                FROM tags t
                JOIN gif_tags gt ON gt.tag_id = t.id
                WHERE gt.gif_id = ?
                ORDER BY t.name
                """,
                (gif_id,),
            )
        ]
        return {
            "id": row["id"],
            "title": row["title"],
            "url": row["url"],
            "nsfw": bool(row["nsfw"]),
            "anime": row["anime"],
            "created_at": row["created_at"],
            "characters": chars,
            "tags": tags,
        }

    def get_gif(self, gif_id: int) -> Dict[str, Any]:
        """
        Holt ein GIF inkl. Characters/Tags.
        """
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM gifs WHERE id = ?", (gif_id,))
            row = cur.fetchone()
            if not row:
                raise KeyError(f"gif id {gif_id} not found")

            chars = [
                r["name"]
                for r in conn.execute(
                    """SELECT c.name FROM characters c
                   JOIN gif_characters gc ON gc.character_id = c.id
                  WHERE gc.gif_id = ? ORDER BY c.name""",
                    (gif_id,),
                )
            ]

            tags = [
                r["name"]
                for r in conn.execute(
                    """SELECT t.name FROM tags t
                   JOIN gif_tags gt ON gt.tag_id = t.id
                  WHERE gt.gif_id = ? ORDER BY t.name""",
                    (gif_id,),
                )
            ]

            return {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "nsfw": bool(row["nsfw"]),
                "anime": row["anime"],
                "created_at": row["created_at"],
                "characters": chars,
                "tags": tags,
            }

    def get_gif_by_url(self, gif_url: str) -> Dict[str, Any]:
        """
        Holt ein GIF (per URL) inkl. Characters/Tags.
        Raises KeyError, wenn nicht gefunden.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gifs WHERE url = ?", (gif_url,)
            ).fetchone()
            if not row:
                raise KeyError(f"gif url {gif_url} not found")

            gif_id = row["id"]

            chars = [
                r["name"]
                for r in conn.execute(
                    """
                    SELECT c.name
                    FROM characters c
                    JOIN gif_characters gc ON gc.character_id = c.id
                    WHERE gc.gif_id = ?
                    ORDER BY c.name
                    """,
                    (gif_id,),
                )
            ]

            tags = [
                r["name"]
                for r in conn.execute(
                    """
                    SELECT t.name
                    FROM tags t
                    JOIN gif_tags gt ON gt.tag_id = t.id
                    WHERE gt.gif_id = ?
                    ORDER BY t.name
                    """,
                    (gif_id,),
                )
            ]

            return {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "nsfw": bool(row["nsfw"]),
                "anime": row["anime"],
                "created_at": row["created_at"],
                "characters": chars,
                "tags": tags,
            }

    def search_by_title(
        self, query: str, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Case-insensitive Teilstring-Suche im Titel.
        """
        like = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM gifs
                WHERE title LIKE ? COLLATE NOCASE
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (like, limit, offset),
            ).fetchall()
            return [self._compose_gif(conn, r) for r in rows]

    def get_random(self) -> Dict[str, Any]:
        """
        Ein zufälliges GIF.
        Hinweis: ORDER BY RANDOM() ist einfach, wird aber bei sehr großen Tabellen langsamer.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gifs ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
            if not row:
                raise KeyError("no gifs found")
            return self._compose_gif(conn, row)

    def get_random_by_tag(self, tag: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT g.*
                FROM gifs g
                JOIN gif_tags gt ON gt.gif_id = g.id
                JOIN tags t ON t.id = gt.tag_id
                WHERE t.name = ? COLLATE NOCASE
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (tag,),
            ).fetchone()
            if not row:
                raise KeyError(f"no gifs found for tag '{tag}'")
            return self._compose_gif(conn, row)

    def get_random_by_anime(self, anime: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM gifs
                WHERE anime = ? COLLATE NOCASE
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (anime,),
            ).fetchone()
            if not row:
                raise KeyError(f"no gifs found for anime '{anime}'")
            return self._compose_gif(conn, row)

    def get_random_by_character(self, character: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT g.*
                FROM gifs g
                JOIN gif_characters gc ON gc.gif_id = g.id
                JOIN characters c ON c.id = gc.character_id
                WHERE c.name = ? COLLATE NOCASE
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (character,),
            ).fetchone()
            if not row:
                raise KeyError(f"no gifs found for character '{character}'")
            return self._compose_gif(conn, row)

    def get_all_tags(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
            ).fetchall()
            return [r["name"] for r in rows]

    def create_token(self, hours_valid: int = 24) -> str:
        token = secrets.token_hex(32)  # 64 Zeichen
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=hours_valid)
        ).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions(token, expires_at) VALUES(?, ?)",
                (token, expires),
            )
        return token

    def validate_token(self, token: str) -> bool:
        if not token:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT token, expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()
            if not row:
                return False
            exp = row["expires_at"]
            if exp is None:
                return True
            try:
                return datetime.now(timezone.utc) < datetime.fromisoformat(exp)
            except Exception:
                return False

    def revoke_token(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

        # ---- Suggestions ----

    def _suggest_from_list(
        self, items: list[str], query: str, limit: int = 5
    ) -> list[str]:
        q = (query or "").strip()
        if not q:
            return []
        qcf = q.casefold()

        # 1) Substring-Treffer zuerst
        subs = [name for name in items if qcf in name.casefold()]

        # 2) Fuzzy (difflib), inkl. Tippfehler
        fuzzy = difflib.get_close_matches(q, items, n=limit * 2, cutoff=0.5)

        # 3) zusammenführen, Duplikate entfernen, limitieren
        out, seen = [], set()
        for name in subs + fuzzy:
            key = name.casefold()
            if key not in seen:
                out.append(name)
                seen.add(key)
            if len(out) >= limit:
                break
        return out

    def list_all_anime(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT anime FROM gifs WHERE anime IS NOT NULL AND TRIM(anime) <> '' ORDER BY anime COLLATE NOCASE"
            ).fetchall()
            return [r["anime"] for r in rows]

    def list_all_characters(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT c.name
                  FROM characters c
                  JOIN gif_characters gc ON gc.character_id = c.id
                 ORDER BY c.name COLLATE NOCASE
                """
            ).fetchall()
            return [r["name"] for r in rows]

    def suggest_anime(self, query: str, limit: int = 5) -> list[str]:
        return self._suggest_from_list(self.list_all_anime(), query, limit)

    def suggest_character(self, query: str, limit: int = 5) -> list[str]:
        return self._suggest_from_list(self.list_all_characters(), query, limit)
