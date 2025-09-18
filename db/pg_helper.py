# db/pg_helper.py
import psycopg
from typing import Iterable, Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
import difflib

DDL = """
CREATE TABLE IF NOT EXISTS gifs (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    nsfw        BOOLEAN NOT NULL,
    anime       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gifs_title       ON gifs(title);
CREATE INDEX IF NOT EXISTS idx_gifs_anime       ON gifs(anime);
CREATE INDEX IF NOT EXISTS idx_gifs_nsfw        ON gifs(nsfw);
CREATE INDEX IF NOT EXISTS idx_gifs_created_at  ON gifs(created_at);

CREATE TABLE IF NOT EXISTS characters (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS tags (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS gif_characters (
    gif_id        INTEGER NOT NULL REFERENCES gifs(id) ON DELETE CASCADE,
    character_id  INTEGER NOT NULL REFERENCES characters(id) ON DELETE RESTRICT,
    PRIMARY KEY (gif_id, character_id)
);
CREATE TABLE IF NOT EXISTS gif_tags (
    gif_id  INTEGER NOT NULL REFERENCES gifs(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE RESTRICT,
    PRIMARY KEY (gif_id, tag_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


class PgGifDB:
    def __init__(self, dsn: str):
        self.dsn = dsn
        # Schema beim Start sicherstellen
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
            conn.commit()

    # ---------- intern ----------
    def _get_or_create(self, cur, table: str, name: str) -> int:
        name = (name or "").strip()
        cur.execute(f"SELECT id FROM {table} WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            f"INSERT INTO {table}(name) VALUES (%s) ON CONFLICT (name) DO NOTHING RETURNING id",
            (name,),
        )
        r = cur.fetchone()
        if r:
            return r[0]
        # Race: nochmal lesen
        cur.execute(f"SELECT id FROM {table} WHERE name = %s", (name,))
        return cur.fetchone()[0]

    def _replace_links(
        self,
        cur,
        link_table: str,
        main_id_col: str,
        ref_id_col: str,
        gif_id: int,
        names: Iterable[str],
        ref_table: str,
    ) -> None:
        cur.execute(f"DELETE FROM {link_table} WHERE {main_id_col} = %s", (gif_id,))
        for n in names or []:
            if not n or not str(n).strip():
                continue
            rid = self._get_or_create(cur, ref_table, str(n))
            cur.execute(
                f"INSERT INTO {link_table}({main_id_col},{ref_id_col}) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (gif_id, rid),
            )

    def _compose_gif(self, cur, row) -> Dict[str, Any]:
        gif_id, title, url, nsfw, anime, created_at = row
        cur.execute(
            """
            SELECT c.name FROM characters c
            JOIN gif_characters gc ON gc.character_id = c.id
            WHERE gc.gif_id = %s ORDER BY c.name
        """,
            (gif_id,),
        )
        chars = [r[0] for r in cur.fetchall()]

        cur.execute(
            """
            SELECT t.name FROM tags t
            JOIN gif_tags gt ON gt.tag_id = t.id
            WHERE gt.gif_id = %s ORDER BY t.name
        """,
            (gif_id,),
        )
        tags = [r[0] for r in cur.fetchall()]

        return {
            "id": gif_id,
            "title": title,
            "url": url,
            "nsfw": bool(nsfw),
            "anime": anime,
            "created_at": created_at.isoformat(),
            "characters": chars,
            "tags": tags,
        }

    # ---------- öffentliche API (gleich wie bei SQLite) ----------
    def insert_gif(
        self,
        *,
        title: str,
        url: str,
        nsfw: bool,
        anime: Optional[str] = None,
        characters: Optional[Iterable[str]] = None,
        tags: Optional[Iterable[str]] = None,
        created_at: Optional[str] = None,
    ) -> int:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            fields = ["title", "url", "nsfw", "anime"]
            vals = [title, url, nsfw, anime]
            if created_at is not None:
                fields.append("created_at")
                vals.append(created_at)  # ISO-8601; Postgres parst TIMESTAMPTZ

            ph = ",".join(["%s"] * len(fields))
            cur.execute(
                f"INSERT INTO gifs({','.join(fields)}) VALUES ({ph}) RETURNING id",
                tuple(vals),
            )
            gif_id = cur.fetchone()[0]

            self._replace_links(
                cur,
                "gif_characters",
                "gif_id",
                "character_id",
                gif_id,
                characters,
                "characters",
            )
            self._replace_links(
                cur, "gif_tags", "gif_id", "tag_id", gif_id, tags, "tags"
            )
            conn.commit()
            return gif_id

    def update_gif(
        self,
        gif_id: int,
        *,
        title=None,
        url=None,
        nsfw=None,
        anime=None,
        characters: Optional[Iterable[str]] = None,
        tags: Optional[Iterable[str]] = None,
    ) -> None:
        sets, vals = [], []
        if title is not None:
            sets.append("title=%s")
            vals.append(title)
        if url is not None:
            sets.append("url=%s")
            vals.append(url)
        if nsfw is not None:
            sets.append("nsfw=%s")
            vals.append(bool(nsfw))
        if anime is not None:
            sets.append("anime=%s")
            vals.append(anime)

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            if sets:
                vals.append(gif_id)
                cur.execute(
                    f"UPDATE gifs SET {', '.join(sets)} WHERE id=%s", tuple(vals)
                )
            if characters is not None:
                self._replace_links(
                    cur,
                    "gif_characters",
                    "gif_id",
                    "character_id",
                    gif_id,
                    characters,
                    "characters",
                )
            if tags is not None:
                self._replace_links(
                    cur, "gif_tags", "gif_id", "tag_id", gif_id, tags, "tags"
                )
            conn.commit()

    def delete_gif(self, gif_id: int) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM gifs WHERE id=%s", (gif_id,))
            conn.commit()

    def get_gif(self, gif_id: int) -> Dict[str, Any]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,title,url,nsfw,anime,created_at FROM gifs WHERE id=%s",
                (gif_id,),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError(f"gif id {gif_id} not found")
            return self._compose_gif(cur, row)

    def get_gif_by_url(self, gif_url: str) -> Dict[str, Any]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,title,url,nsfw,anime,created_at FROM gifs WHERE url=%s",
                (gif_url,),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError(f"gif url {gif_url} not found")
            return self._compose_gif(cur, row)

    def search_by_title(
        self, query: str, nsfw_mode: str = "false", limit: int = 50, offset: int = 0
    ):
        where = []
        params: List[Any] = []
        if query:
            where.append("title ILIKE %s")
            params.append(f"%{query}%")
        # NSFW-Filter
        m = (nsfw_mode or "false").lower()
        if m == "only":
            where.append("nsfw = TRUE")
        elif m == "false":
            where.append("nsfw = FALSE")
        # TRUE => kein extra Filter

        sql = "SELECT id,title,url,nsfw,anime,created_at FROM gifs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            return [self._compose_gif(cur, r) for r in rows]

    def get_random(self, nsfw_mode: str = "false"):
        cond = {"only": "nsfw=TRUE", "false": "nsfw=FALSE"}.get(
            (nsfw_mode or "false").lower(), "TRUE"
        )
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id,title,url,nsfw,anime,created_at FROM gifs WHERE {cond} ORDER BY random() LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                raise KeyError("no gifs found")
            return self._compose_gif(cur, row)

    def get_random_by_tag(self, tag: str, nsfw_mode: str = "false"):
        cond = {"only": "g.nsfw=TRUE", "false": "g.nsfw=FALSE"}.get(
            (nsfw_mode or "false").lower(), "TRUE"
        )
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT g.id,g.title,g.url,g.nsfw,g.anime,g.created_at
                  FROM gifs g
                  JOIN gif_tags gt ON gt.gif_id=g.id
                  JOIN tags t ON t.id=gt.tag_id
                 WHERE t.name = %s AND {cond}
              ORDER BY random() LIMIT 1
            """,
                (tag,),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError(f"no gifs found for tag '{tag}'")
            return self._compose_gif(cur, row)

    def get_random_by_anime(self, anime: str, nsfw_mode: str = "false"):
        cond = {"only": "nsfw=TRUE", "false": "nsfw=FALSE"}.get(
            (nsfw_mode or "false").lower(), "TRUE"
        )
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id,title,url,nsfw,anime,created_at
                  FROM gifs
                 WHERE anime ILIKE %s AND {cond}
              ORDER BY random() LIMIT 1
            """,
                (anime,),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError(f"no gifs found for anime '{anime}'")
            return self._compose_gif(cur, row)

    def get_random_by_character(self, character: str, nsfw_mode: str = "false"):
        cond = {"only": "g.nsfw=TRUE", "false": "g.nsfw=FALSE"}.get(
            (nsfw_mode or "false").lower(), "TRUE"
        )
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT g.id,g.title,g.url,g.nsfw,g.anime,g.created_at
                  FROM gifs g
                  JOIN gif_characters gc ON gc.gif_id=g.id
                  JOIN characters c ON c.id=gc.character_id
                 WHERE c.name ILIKE %s AND {cond}
              ORDER BY random() LIMIT 1
            """,
                (character,),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError(f"no gifs found for character '{character}'")
            return self._compose_gif(cur, row)

    def get_all_tags(self, nsfw_mode: str = "false"):
        cond = {"only": "g.nsfw=TRUE", "false": "g.nsfw=FALSE"}.get(
            (nsfw_mode or "false").lower(), "TRUE"
        )
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT t.name
                  FROM tags t
                  JOIN gif_tags gt ON gt.tag_id = t.id
                  JOIN gifs g ON g.id = gt.gif_id
                 WHERE {cond}
              ORDER BY t.name
            """
            )
            return [r[0] for r in cur.fetchall()]

    # --- Sessions (Tokens) ---
    def create_token(self, hours_valid: int = 24) -> str:
        import secrets

        token = secrets.token_hex(32)
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=hours_valid)
        ).isoformat()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions(token, expires_at) VALUES (%s,%s)",
                (token, expires),
            )
            conn.commit()
        return token

    def validate_token(self, token: str) -> bool:
        if not token:
            return False
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT expires_at FROM sessions WHERE token=%s", (token,))
            row = cur.fetchone()
            if not row:
                return False
            exp = row[0]  # TIMESTAMPTZ → datetime
            return (exp is None) or (datetime.now(timezone.utc) < exp)

    def revoke_token(self, token: str) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token=%s", (token,))
            conn.commit()

    # ---- Suggest Helpers (identisch zu SQLite-Variante) ----
    def list_all_anime(self) -> list[str]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT anime FROM gifs WHERE anime IS NOT NULL AND TRIM(anime) <> '' ORDER BY anime"
            )
            return [r[0] for r in cur.fetchall()]

    def list_all_characters(self) -> list[str]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT c.name
                  FROM characters c
                  JOIN gif_characters gc ON gc.character_id = c.id
                 ORDER BY c.name
            """
            )
            return [r[0] for r in cur.fetchall()]

    def _suggest_from_list(
        self, items: list[str], query: str, limit: int = 5
    ) -> list[str]:
        q = (query or "").strip()
        if not q:
            return []
        qcf = q.casefold()
        subs = [name for name in items if qcf in name.casefold()]
        fuzzy = difflib.get_close_matches(q, items, n=limit * 2, cutoff=0.5)
        out, seen = [], set()
        for name in subs + fuzzy:
            key = name.casefold()
            if key not in seen:
                out.append(name)
                seen.add(key)
            if len(out) >= limit:
                break
        return out

    def suggest_anime(self, query: str, limit: int = 5) -> list[str]:
        return self._suggest_from_list(self.list_all_anime(), query, limit)

    def suggest_character(self, query: str, limit: int = 5) -> list[str]:
        return self._suggest_from_list(self.list_all_characters(), query, limit)
