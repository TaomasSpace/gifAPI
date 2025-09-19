# main.py
from fastapi import FastAPI, HTTPException, Query, Header, Depends
from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional
from db.db_helper import GifDB
from pathlib import Path
from pydantic import BaseModel
import os
from fastapi import Depends, Header
from db.db_helper import GifDB as SqliteGifDB
from db.pg_helper import PgGifDB  # <— neu
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Response


# --- Pydantic Modelle ---
class GifIn(BaseModel):
    title: str
    url: HttpUrl
    nsfw: bool
    anime: Optional[str] = None
    characters: List[str] = []
    tags: List[str] = []


class GifUpdate(BaseModel):
    title: Optional[str] = None
    url: Optional[HttpUrl] = None
    nsfw: Optional[bool] = None
    anime: Optional[str] = None
    characters: Optional[List[str]] = None  # None = nicht ändern, [] = leeren
    tags: Optional[List[str]] = None  # None = nicht ändern, [] = leeren


class GifOut(BaseModel):
    id: int
    title: str
    url: HttpUrl
    nsfw: bool
    anime: Optional[str]
    created_at: str
    characters: List[str]
    tags: List[str]


class LoginIn(BaseModel):
    password: str


class LoginOut(BaseModel):
    token: str
    expires_at: str | None = None


# --- App + DB ---
app = FastAPI(title="Anime GIF API", version="0.1.0")
DATABASE_URL = os.getenv("DATABASE_URL")  # von Render
db = PgGifDB(DATABASE_URL) if DATABASE_URL else SqliteGifDB("gifs.db")
from fastapi.middleware.cors import CORSMiddleware

ADMIN_PASSWORD = os.getenv("GIFAPI_ADMIN_PASSWORD", "")


def require_auth(x_auth_token: str | None = Header(default=None, alias="X-Auth-Token")):
    if not x_auth_token or not db.validate_token(x_auth_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


ALLOWED_ORIGINS = [
    o.strip()
    for o in (
        os.getenv("ALLOWED_ORIGINS")
        or "https://gifapi-tqh4.onrender.com,http://127.0.0.1:8000,http://localhost:8000"
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,  # wir nutzen Header-Token, keine Cookies
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Auth-Token"],
)


@app.get("/auth/verify")
def verify(x_auth_token: str | None = Header(default=None, alias="X-Auth-Token")):
    if not x_auth_token or not db.validate_token(x_auth_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    # optional Ablaufzeit zurückgeben (falls DB das kann)
    exp = None
    try:
        exp = db.get_token_expiry(x_auth_token)
    except AttributeError:
        pass
    return {"ok": True, "expires_at": exp}


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    # neue Datei: admin.html (siehe unten)
    path = Path(__file__).resolve().parents[1] / "admin.html"
    return path.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def root():
    path = Path(__file__).resolve().parents[1] / "index.html"
    return path.read_text(encoding="utf-8")


@app.get("/admin/gifs", dependencies=[Depends(require_auth)])
def admin_list_gifs(
    q: Optional[str] = Query("", description="Titel-Contains, leer = alle"),
    nsfw: str = Query("true", description="false|true|only"),  # true = beides
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    # beide Backends unterstützen LIKE/ILIKE '%%'
    return db.search_by_title(q or "", nsfw_mode=nsfw, limit=limit, offset=offset)


@app.post("/auth/login", response_model=LoginOut)
def login(payload: LoginIn):
    if not ADMIN_PASSWORD:
        # 500 ist okay, aber sag klar was fehlt
        raise HTTPException(500, "Server not configured: GIFAPI_ADMIN_PASSWORD missing")

    if payload.password != ADMIN_PASSWORD:
        raise HTTPException(401, "Invalid password")

    token = db.create_token(hours_valid=24)
    expires = None
    try:
        expires = db.get_token_expiry(token)
    except AttributeError:
        expires = None

    return {"token": token, "expires_at": expires}


@app.post("/auth/logout")
def logout(x_auth_token: str | None = Header(default=None, alias="X-Auth-Token")):
    if not x_auth_token:
        raise HTTPException(400, "Missing token")
    db.revoke_token(x_auth_token)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def root():
    path = Path(__file__).resolve().parents[1] / "index.html"
    return path.read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/gifs")  # muss vor /gifs/{gif_id} stehen
def unified_get_gifs(
    q: Optional[str] = Query(None, description="Title contains (case-insensitive)"),
    tag: Optional[str] = Query(None, description="Pick random by tag"),
    anime: Optional[str] = Query(None, description="Pick random by anime"),
    character: Optional[str] = Query(None, description="Pick random by character"),
    list: Optional[str] = Query(
        None, alias="list", description="Use 'tags' to list all tags"
    ),
    nsfw: str = Query("false", description="false|true|only"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    nsfw_mode = (nsfw or "false").lower()
    if nsfw_mode not in {"false", "true", "only"}:
        raise HTTPException(
            status_code=400, detail="nsfw must be one of: false, true, only"
        )

    if list is not None:
        if list.lower() == "tags":
            return db.get_all_tags(nsfw_mode=nsfw_mode)
        raise HTTPException(
            status_code=400, detail="Unsupported list value. Use list=tags"
        )

    if q is not None:
        if any([tag, anime, character]):
            raise HTTPException(
                status_code=400, detail="Use either q OR one of tag/anime/character."
            )
        return db.search_by_title(q, nsfw_mode=nsfw_mode, limit=limit, offset=offset)

    filters_set = [x for x in [tag, anime, character] if x]
    if len(filters_set) > 1:
        raise HTTPException(
            status_code=400, detail="Use only one of tag, anime, or character."
        )

    try:
        if tag:
            return db.get_random_by_tag(tag, nsfw_mode=nsfw_mode)
        if anime:
            return db.get_random_by_anime(anime, nsfw_mode=nsfw_mode)
        if character:
            return db.get_random_by_character(character, nsfw_mode=nsfw_mode)
    except KeyError:
        if anime:
            suggestions = db.suggest_anime(anime, limit=5)
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"no gifs found for anime '{anime}'",
                    "suggestions": {"meintest_du": suggestions},
                },
            )
        if character:
            suggestions = db.suggest_character(character, limit=5)
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"no gifs found for character '{character}'",
                    "suggestions": {"meintest_du": suggestions},
                },
            )
        raise

    # Default: komplett random, mit NSFW-Filter
    try:
        return db.get_random(nsfw_mode=nsfw_mode)
    except KeyError:
        raise HTTPException(status_code=404, detail="no gifs in database")


@app.post(
    "/gifs",
    response_model=GifOut,
    status_code=201,
    dependencies=[Depends(require_auth)],
)
def create_or_update_gif(payload: GifIn):
    try:
        # existiert diese URL schon?
        existing = None
        try:
            existing = db.get_gif_by_url(str(payload.url))
        except KeyError:
            pass

        if existing:
            # Update → 200
            db.update_gif(
                existing["id"],
                title=payload.title,
                nsfw=payload.nsfw,
                anime=payload.anime,
                characters=payload.characters,
                tags=payload.tags,
            )
            return db.get_gif(existing["id"])  # FastAPI überschreibt den Code nicht

        # Insert → 201 (default des Decorators)
        gif_id = db.insert_gif(
            title=payload.title,
            url=str(payload.url),
            nsfw=payload.nsfw,
            anime=payload.anime,
            characters=payload.characters,
            tags=payload.tags,
        )
        return db.get_gif(gif_id)

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/gifs/{gif_id}", response_model=GifOut)
def read_gif(gif_id: int):
    try:
        return db.get_gif(gif_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="GIF not found")


@app.patch(
    "/gifs/{gif_id}", response_model=GifOut, dependencies=[Depends(require_auth)]
)
def update_gif(gif_id: int, payload: GifUpdate):
    # Prüfen, ob existiert
    try:
        _ = db.get_gif(gif_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="GIF not found")

    # Update ausführen
    db.update_gif(
        gif_id,
        title=payload.title,
        url=str(payload.url) if payload.url is not None else None,
        nsfw=payload.nsfw,
        anime=payload.anime,
        characters=payload.characters,
        tags=payload.tags,
    )
    return db.get_gif(gif_id)


@app.delete("/gifs/{gif_id}", status_code=204, dependencies=[Depends(require_auth)])
def delete_gif(gif_id: int):
    try:
        _ = db.get_gif(gif_id)  # 404, falls nicht vorhanden
    except KeyError:
        raise HTTPException(status_code=404, detail="GIF not found")
    db.delete_gif(gif_id)
    return Response(status_code=204)
