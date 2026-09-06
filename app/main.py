"""The FastAPI application: every route the site has.

⚠ The server listens on 127.0.0.1 only (see deploy/family.service). On
0.0.0.0 it would be reachable from the whole network and past every lock in
here -- the proxy in front is not decoration.
"""
import json
import logging
import os
import shutil

from datetime import datetime, timedelta

from fastapi import (Body, FastAPI, File, Form, HTTPException, Request,
                     Response, UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import (acl, album, auth, collections, config, db, devices, gallery, geo,
               journey, members, register, scan, security, serve, shares, sync,
               tagging, tiles, tileseed, tree, upload)
from . import guests
from . import convert as _convert          # registers the convert handler
from .worker import Worker, enqueue, requeue_orphans

app = FastAPI(title="Family-Website", docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(security.gate)

_worker = Worker()
templates = Jinja2Templates(directory=str(config.TEMPLATE_DIR))
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


def _static_ver() -> str:
    """A version for the `?v=` on the CSS and JS addresses. Computed from the
    newest static file, so a browser picks up new CSS after a deploy without a
    hard reload. Once, at startup -- the service is restarted by every
    deploy."""
    try:
        return str(int(max(p.stat().st_mtime
                           for p in config.STATIC_DIR.rglob("*") if p.is_file())))
    except (OSError, ValueError):
        return "1"


templates.env.globals["static_ver"] = _static_ver()


def _share_page(request: Request, name: str, status: int = 200, **ctx):
    """The context for a share page.

    ⚠ **Nothing** from the site goes in here: no album list, no counts, no
    user name, no navigation. Whoever gets a link sees the collection and
    nothing else -- a share link is not half an account.
    """
    return templates.TemplateResponse(
        request, name, {"site_title": config.SITE_TITLE, **ctx},
        status_code=status)


def _page(request: Request, name: str, **ctx):
    """The album list is on EVERY page -- so it belongs here and not in each
    route separately."""
    ident = security.identify(request)
    st = ctx.pop("structure", None)
    if st is None:
        # ⚠ With `ident`: the album list on the page is also a list of albums.
        # Without the viewer here the navigation would name albums that this
        # person is not allowed to open at all.
        st = gallery.structure(ident)
    counts = {y: sum(n for c in cs.values() for n in c.values()) for y, cs in st.items()}
    return templates.TemplateResponse(request, name, {
        # ⚠ The admin page does not show the album list. It is navigation for
        # looking; where work is done it is only in the way, and it takes a
        # whole column.
        "no_drawer": request.url.path.startswith("/admin"),
        "site_title": config.SITE_TITLE, "is_admin": ident.is_admin,
        "is_contributor": ident.is_contributor,
        "user": ident.user, "email": ident.email, "q": ctx.pop("q", None),
        # ⚠ Signing out does not mean the same everywhere: locally the session
        #   is deleted, behind a proxy THAT session has to end.
        "logout_url": ("/logout" if config.AUTH_LOCAL else
                       os.environ.get("FAMILY_SSO_LOGOUT",
                                      "/outpost.goauthentik.io/sign_out")),
        "structure": st, "counts": counts,
        "here": ctx.pop("here", {}), **ctx})


@app.on_event("startup")
def _startup() -> None:
    config.check_startup()
    config.ensure_dirs()
    config.ensure_marker(config.ORIGIN_DIR)
    config.ensure_marker(config.WEB_DIR)
    db.init()
    auth.sweep()          # expired sessions can go
    n = requeue_orphans()
    if n:
        logging.getLogger("family").warning(
            "%d job(s) were left on `running` -- they run once more", n)
    _worker.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _worker.stop()


def _tree_ok(path) -> dict:
    """A tree is in order when it is a mount point AND the marker file is there.

    The second half is the point: a share that is not mounted looks exactly
    like an empty folder, and an empty list means "every photograph was
    removed" to the sync."""
    out = {"path": str(path), "exists": path.is_dir()}
    if config.REQUIRE_MOUNT:
        out["mountpoint"] = os.path.ismount(path)
    out["marker"] = (path / config.MARKER_NAME).is_file()
    out["ok"] = out["exists"] and out["marker"] and out.get("mountpoint", True)
    if out["ok"]:
        st = shutil.disk_usage(path)
        out["free_gb"] = round(st.free / 1024 ** 3, 1)
    return out


def _state() -> dict:
    """The whole state. For the admin page and for monitoring on 127.0.0.1 --
    not for anyone from outside."""
    origins = _tree_ok(config.ORIGIN_DIR)
    web = _tree_ok(config.WEB_DIR)
    problems = []
    if not origins["ok"]:
        problems.append("origins")
    if not web["ok"]:
        problems.append("web")
    # ⚠ Only in proxy mode. A local installation has no shared secret, and
    #   without this distinction a fresh installation would stand at "not ok"
    #   for ever -- and the Docker health check would restart the container
    #   again and again.
    if config.AUTH_PROXY and not config.proxy_secret():
        problems.append("proxy-secret")
    return {
        "ok": not problems, "version": __import__("app").__version__,
        "problems": problems, "origins": origins, "web": web,
        "counts": db.counts(), "worker": _worker.status(),
        "shares_enabled": config.SHARES_ENABLED, "require_auth": config.REQUIRE_AUTH,
    }


@app.get("/api/health")
def health(request: Request):
    """From outside: a bare ok. From 127.0.0.1: the whole state.

    `problems` gives away mount paths and how much is stored -- nobody looking
    in from outside needs that."""
    ident = security.identify(request)
    st = _state()
    if not ident.local:
        return {"ok": st["ok"]}
    st["seen"] = {"user": ident.user, "groups": list(ident.groups),
                  "client": security.client_ip(request)}
    return st


@app.get("/robots.txt")
def robots():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("User-agent: *\nDisallow: /\n")




# --------------------------------------------------------------------------
#  Upload — four fields, and from there it runs by itself
# --------------------------------------------------------------------------

def _admin(request: Request):
    """Uploading is admin work. The group comes out of the header the proxy
    sets -- a client cannot bring it along (see app/security.py)."""
    # A foreign page can fire a POST with your cookie. `form-action` only
    # protects our own forms, so here: everything that changes something has
    # to come from our own page.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin") or ""
        ok = (site in ("same-origin", "none")
              or (not site and (not origin or origin.rstrip("/") == config.SITE_URL)))
        if not ok:
            raise HTTPException(status_code=403, detail="cross-site request refused")
    ident = security.identify(request)
    if not ident.is_admin:
        # What actually arrived goes into the log -- not into the answer.
        # Without it we would be guessing whether the group is missing or the
        # header never arrived at all.
        logging.getLogger("family").warning(
            "admin refused: user=%r groups=%r expected=%r",
            ident.user, ident.groups, sorted(config.ADMIN_GROUPS))
        raise HTTPException(status_code=403,
                            detail="only for " + " or ".join(sorted(config.ADMIN_GROUPS)))
    return ident


def _contributor(request: Request):
    """For the paths an account holder may take themselves: uploading and
    managing their own photographs. The same cross-site shield as `_admin`,
    but the group is the contributor group -- and every admin is a contributor
    too."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin") or ""
        ok = (site in ("same-origin", "none")
              or (not site and (not origin or origin.rstrip("/") == config.SITE_URL)))
        if not ok:
            raise HTTPException(status_code=403, detail="cross-site request refused")
    ident = security.identify(request)
    if not ident.is_contributor:
        raise HTTPException(status_code=403, detail="you need an account to do that")
    return ident


def _owns(ident, photo_ids) -> list:
    """Of the photographs asked for, the ones this person REALLY may manage.

    ⚠ An admin may do anything. A contributor ONLY what they uploaded
    themselves (`owner = their name`) -- not what somebody else uploaded, and
    not what they merely happen to be tagged in. Returns the list of allowed
    ids; whatever does not belong falls away quietly, and the route then
    decides whether that is an error."""
    ids = [int(i) for i in (photo_ids or [])]
    if not ids:
        return []
    if getattr(ident, "is_admin", False):
        return ids
    q = ",".join("?" * len(ids))
    return [r["id"] for r in db.connect().execute(
        f"SELECT id FROM photos WHERE id IN ({q}) AND owner=?",
        (*ids, ident.user))]


@app.get("/api/tree")
def api_tree(request: Request):
    """What already exists: years, countries, events, places.

    `all_events` is the flat list -- for the case where no year has been
    picked yet: the album should still be findable, and picking it fills in
    the path.
    """
    _admin(request)
    out = tree.overview()
    conn = db.connect()
    out["places"] = [r[0] for r in conn.execute(
        "SELECT DISTINCT place FROM photos WHERE place IS NOT NULL AND place<>'' "
        "ORDER BY place")]
    places_by_event = {}
    for path, place in conn.execute(
            "SELECT origin_path, place FROM photos "
            "WHERE place IS NOT NULL AND place<>''"):
        parts = (path or "").split("/")
        if len(parts) >= 4:
            places_by_event.setdefault(parts[2], place)
    flat = []
    for year, countries in out["years"].items():
        for country, events in countries.items():
            for ev in events:
                flat.append({"event": ev, "year": year, "country": country,
                             "place": places_by_event.get(ev, "")})
    flat.sort(key=lambda e: (e["year"], e["country"], e["event"]), reverse=True)
    out["all_events"] = flat
    return out


@app.post("/api/upload/batch")
def api_batch_new(request: Request):
    _contributor(request)
    return {"batch": upload.create_batch()}


@app.post("/api/upload/{batch}/file")
def api_file_new(batch: str, request: Request, body: dict = Body(...)):
    _contributor(request)
    try:
        return upload.add_file(batch, body.get("name", ""), int(body.get("size", 0)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/upload/{batch}/file/{file_id}/chunk")
async def api_chunk(batch: str, file_id: int, request: Request, offset: int = 0):
    _contributor(request)
    data = await request.body()
    try:
        return {"have": upload.append_chunk(batch, file_id, offset, data)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/upload/{batch}/file/{file_id}/done")
def api_file_done(batch: str, file_id: int, request: Request):
    _contributor(request)
    try:
        return upload.finish_file(batch, file_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/upload/{batch}/proposal")
def api_proposal(batch: str, request: Request):
    """The suggestion plus both paths, exactly as they stand on the screen."""
    _contributor(request)
    out = upload.proposal(batch)
    p = out.get("proposal") or {}
    if p.get("year") and p.get("country") and p.get("event"):
        try:
            out["paths"] = {
                "origin": str(tree.target_dir(p["year"], p["country"], p["event"])),
                "web": str(tree.web_dir(p["year"], p["country"], p["event"])),
            }
        except ValueError:
            out["paths"] = None
    return out


@app.post("/api/upload/{batch}/commit")
def api_commit(batch: str, request: Request, body: dict = Body(...)):
    """The four fields are confirmed. From here on it writes.

    ⚠ An admin uploads into the library (the original stays in the originals
    tree, owner=NULL). A contributor uploads THEIR OWN photographs: owner=their
    name, and the original is thrown away after the conversion."""
    ident = _contributor(request)
    try:
        return upload.commit(
            batch,
            year=str(body.get("year", "")),
            country=str(body.get("country", "")),
            event=str(body.get("event", "")),
            place=str(body.get("place", "")),
            include_duplicates=bool(body.get("include_duplicates", False)),
            owner=None if ident.is_admin else ident.user,
            keep_original=ident.is_admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------
#  The gallery. Everything here sits behind the authentication.
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def page_index(request: Request):
    who = security.identify(request)
    st = gallery.structure(who)
    total = sum(n for cs in st.values() for c in cs.values() for n in c.values())
    al = gallery.albums(who)
    import json as _json
    points = gallery.map_points(who)
    hero = gallery.cover(viewer=who)
    # ⚠ They are in an admin group, but their SESSION does not carry it. The
    # site would be empty for them and they would not know why -- so they are
    # told, instead of being shown a blank page.
    stale_admin = (not who.is_admin and total == 0
                   and who.user in members.admin_names())
    return _page(request, "index.html", structure=st, total=total, albums=al,
                 stale_admin=stale_admin,
                 hero_width=(gallery.photo(hero, viewer=who) or {}).get("width")
                            if hero else None,
                 album_count=len(al), hero=hero, hero_rev=gallery.rev(hero),
                 strips=gallery.strips(8, viewer=who),
                 wall=gallery.list_photos({}, 1, 40, viewer=who),
                 map_points=points,
                 map_max_zoom=(config.TILE_SEED_MAX_ZOOM if config.TILE_OFFLINE
                               else tiles.MAX_ZOOM),
                 map_json=_json.dumps(points).replace("</", "<\\/"))


@app.get("/y/{year}", response_class=HTMLResponse)
def page_year(request: Request, year: str, page: int = 1):
    res = gallery.list_photos({"year": year}, page,
                              viewer=security.identify(request), pair=False)
    return _page(request, "album.html", res=res, heading=year,
                 subplate=f"{res['total']} plate" + ("" if res["total"] == 1 else "s"),
                 submeta=[f"{res['total']} photographs"],
                 here={"year": year}, base=f"/y/{year}?")


@app.get("/y/{year}/{country}/{event}", response_class=HTMLResponse)
def page_event(request: Request, year: str, country: str, event: str, page: int = 1):
    f = {"year": year, "country": country, "event": event}
    who = security.identify(request)
    # ⚠ 404 and not an empty page: "does not exist" gives away less than
    # "exists, but not for you". The address could have been guessed.
    if not acl.may_see(who, year, country, event):
        raise HTTPException(status_code=404, detail="not found")
    res = gallery.list_photos(f, page, viewer=who, pair=False)
    # ⚠ An album that does not exist has to answer EXACTLY like one you have
    # no rights to. Otherwise the difference is an instruction manual: try an
    # address, and 404 against 200 tells you whether it is there.
    if res["total"] == 0:
        raise HTTPException(status_code=404, detail="not found")
    place = res["photos"][0]["place"] if res["photos"] else None
    # ⚠ The country now stands behind the number of plates (album.html), so
    # NOT in the sub-line any more -- it would be there twice.
    sub = []
    if place and place not in gallery.album_title(year, event):
        sub.append(place)
    # Whoever may look at an album may also share it (may_see is already
    # checked on the GET). A normal user's link works once, an admin sets it
    # themselves -- see /api/albums/share.
    can_share = who.is_admin or acl.may_see(who, year, country, event)
    return _page(request, "album.html", res=res,
                 days=gallery.by_day(res["photos"]),
                 heading=gallery.album_title(year, event),
                 subplate=f"{res['total']} plate" + ("" if res["total"] == 1 else "s"),
                 submeta=sub,
                 here={"year": year, "event": event},
                 album={"year": year, "country": country, "event": event},
                 can_share=can_share,
                 can_contribute=bool(who.user) and acl.may_see(who, year, country, event),
                 journey=journey.get_journey(year, country, event),
                 base=f"/y/{year}/{country}/{event}?")


@app.get("/y/{year}/{country}/{event}/all.zip")
def album_zip_all(request: Request, year: str, country: str, event: str):
    """The whole album as ONE zip -- for a family member with an account who
    may look at it. GPS stays in (they are their own family photographs); a
    guest link strips GPS out, which is a different matter (see /s/.../all.zip)."""
    who = security.identify(request)
    if not acl.may_see(who, year, country, event):
        raise HTTPException(status_code=404, detail="not found")
    f = {"year": year, "country": country, "event": event}
    res = gallery.list_photos(f, page=1, page_size=100000, viewer=who, pair=False)
    if res["total"] == 0:
        raise HTTPException(status_code=404, detail="not found")
    from fastapi.responses import FileResponse as _FR
    zpath = guests.zip_all(res["photos"], keep_gps=True)
    name = gallery.album_title(year, event) or "album"
    return _FR(zpath, media_type="application/zip", filename=f"{name}.zip",
               headers=guests._HEADERS, background=guests._delete_later(zpath))


@app.post("/y/{year}/{country}/{event}/contribute")
async def album_contribute(request: Request, year: str, country: str, event: str):
    """A member with an account adds a photograph to this album -- **live**,
    no approval. Only a guest coming through a share link (/s/) has to be
    approved (see guests.receive_sync). The checks (type from content, virus
    scan) run all the same, and an original in the originals tree is never
    touched (only NEW ones are created).

    ⚠ As with the guest upload: no `File(...)` dependency (the whole body
    would be read BEFORE we check the rights), and `run_in_threadpool`
    because the checks (type, virus scan, conversion) block."""
    who = security.identify(request)
    # Only a registered user who may look at this album.
    if not who.user or not acl.may_see(who, year, country, event):
        raise HTTPException(status_code=404, detail="not found")
    form = await request.form()
    fname = form.get("file")
    if fname is None or not hasattr(fname, "read"):
        raise HTTPException(status_code=400, detail="no file")
    try:
        return await run_in_threadpool(
            guests.contribute_live, year, country, event, who.user, fname)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/search", response_class=HTMLResponse)
def page_search(request: Request, q: str = "", page: int = 1):
    res = gallery.list_photos({"q": q}, page,
                              viewer=security.identify(request), pair=False) if q else {
        "total": 0, "page": 1, "pages": 1, "photos": []}
    return _page(request, "album.html", res=res, q=q,
                 heading=f"“{q}”" if q else "Search",
                 subplate=(f"{res['total']} plate" + ("" if res["total"] == 1 else "s")
                           if q else "search"),
                 submeta=(["date", "place", "camera", "file name", "tags", "people"]
                          if not q else []),
                 base=f"/search?q={q}&")


@app.get("/mine", response_class=HTMLResponse)
def page_mine(request: Request):
    """The home page for an account holder: their own photographs, albums,
    collections and links. An admin is sent to /admin (they see everything)."""
    ident = _contributor(request)
    if ident.is_admin:
        return RedirectResponse("/admin", status_code=307)
    return _page(request, "mine.html", me=ident.user,
                 albums=album.list_all(ident.user),
                 sets=collections.all_of(ident.user),
                 links=shares.all_of(ident.user),
                 counts={"photos": db.connect().execute(
                     "SELECT COUNT(*) FROM photos WHERE owner=? AND state='ok'",
                     (ident.user,)).fetchone()[0]})


@app.get("/my/albums", response_class=HTMLResponse)
def page_my_albums(request: Request):
    """Every album this user may look at -- to download from and to add
    photographs to. What they may manage themselves additionally gets an
    "edit" link into the workshop."""
    who = security.identify(request)
    if not who.user:
        raise HTTPException(status_code=403, detail="you need an account to do that")
    mine = None if who.is_admin else who.user
    own = {(a["year"], a["country"], a["event"]) for a in album.list_all(mine)}
    return _page(request, "my_albums.html",
                 albums=gallery.albums(viewer=who), own_keys=own,
                 is_contributor=who.is_contributor)


@app.get("/admin", response_class=HTMLResponse)
def page_admin(request: Request):
    _admin(request)
    return _page(request, "admin.html", health=_state(), albums=gallery.albums())


@app.get("/api/infected")
def api_infected(request: Request):
    """The files the virus scan stopped -- for the admin."""
    _admin(request)
    return {"photos": [dict(r) for r in db.connect().execute(
        "SELECT id, origin_path, note, created_at FROM photos "
        "WHERE state='infected' ORDER BY id DESC LIMIT 200")]}


@app.post("/api/scan")
def api_scan(request: Request):
    """Walk the tree and take in whatever is not in the database yet. Reads
    only -- no file is created, changed or deleted.

    ⚠ Runs as a JOB, not synchronously: a scan across the whole tree takes
    minutes, and an HTTP request that waits that long gets a 504 (gateway
    timeout). The progress shows in the photo counts on the admin page."""
    _admin(request)
    return {"job": enqueue("scan"), "started": True}


@app.get("/api/scan")
def api_scan_state(request: Request):
    """The progress of the running scan: how far along, and where it is."""
    _admin(request)
    import json as _j
    raw = db.get_state("scan_progress")
    try:
        prog = _j.loads(raw) if raw else {"running": False}
    except (ValueError, TypeError):
        prog = {"running": False}
    return {"progress": prog}


@app.get("/admin/sync", response_class=HTMLResponse)
def page_sync(request: Request):
    """The sync: what the last runs found, and the buttons."""
    _admin(request)
    return _page(request, "sync.html", runs=sync.last_runs(12),
                 halt=sync.pending_halt(), trash=_trash_state(),
                 counts=db.counts())


def _trash_state() -> dict:
    """What lies in the bin -- without touching any of it."""
    d = config.WEB_DIR / sync.TRASH_DIR
    if not d.is_dir():
        return {"days": config.TRASH_DAYS, "buckets": []}
    out = []
    for day_dir in sorted(d.iterdir(), reverse=True):
        if day_dir.is_dir():
            out.append({"tag": day_dir.name,
                        "n": sum(1 for f in day_dir.rglob("*") if f.is_file())})
    return {"days": config.TRASH_DAYS, "buckets": out}


@app.post("/api/sync")
def api_sync(request: Request, body: dict = Body(default={})):
    """Start a run. It runs as a job -- a deep run through 30,000 photographs
    takes longer than an HTTP request."""
    _admin(request)
    kind = str(body.get("kind", "quick"))
    if kind not in ("quick", "deep"):
        raise HTTPException(status_code=400, detail="kind must be quick or deep")
    payload = kind + ("!" if body.get("confirm_missing") else "")
    return {"job": enqueue("sync", payload), "kind": kind,
            "confirmed": bool(body.get("confirm_missing"))}


@app.get("/api/sync")
def api_sync_state(request: Request):
    _admin(request)
    return {"runs": sync.last_runs(12), "halt": sync.pending_halt(),
            "worker": _worker.status()}


@app.get("/admin/albums", response_class=HTMLResponse)
def page_albums(request: Request):
    """The workshop: every album, and every field editable.

    The scan hangs in here too -- because that is exactly the route: copy a
    folder into the originals tree, scan, and then put the fields right."""
    ident = _contributor(request)
    mine = None if ident.is_admin else ident.user
    return _page(request, "albums.html", albums=album.list_all(mine),
                 facets=gallery.facets(), me=ident.user, is_admin=ident.is_admin,
                 acls=acl.all_acls(), members=members.pickable(),
                 groups=members.group_choices(),
                 admins=members.admin_names(),
                 people=tagging.all_of("person"), tags=tagging.all_of("tag"),
                 tagpick=tagging.tag_choices(),
                 peoplepick=tagging.person_choices(),
                 tags_on=album.tags_of_albums())


@app.post("/api/albums/edit")
def api_album_edit(request: Request, body: dict = Body(...)):
    """Change year, country, name, place -- in the trees and in the database.

    ⚠ An admin may put any album right. A contributor ONLY an album that
    contains NOTHING BUT their own photographs -- otherwise they would move
    the admin's (or another user's) photographs along with it."""
    ident = _contributor(request)
    if not ident.is_admin:
        y, c, e = str(body.get("year", "")), str(body.get("country", "")), str(body.get("event", ""))
        rows = db.connect().execute(
            "SELECT owner FROM photos WHERE coalesce(album_year,'—')=? "
            "AND coalesce(country,'—')=? AND coalesce(event,'—')=?", (y, c, e)).fetchall()
        if not rows or any((r["owner"] or "") != ident.user for r in rows):
            raise HTTPException(status_code=403, detail="that is not your album")
    def was_set(key):
        return str(body[key]) if key in body else None
    try:
        return album.edit(
            str(body.get("year", "")), str(body.get("country", "")),
            str(body.get("event", "")),
            new_year=was_set("new_year"),
            new_country=was_set("new_country"),
            new_event=was_set("new_event"),
            new_place=str(body.get("new_place", "")),
        )
    except album.AlbumBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/albums/remove")
def api_album_remove(request: Request, body: dict = Body(...)):
    """Take a WHOLE album off the site (all of its photographs). The originals
    are NOT touched -- and the sha256 stays in `removed`, so the next scan
    does not pick them up again.

    ⚠ An admin may take any album; an account holder ONLY an album with THEIR
    photographs."""
    ident = _contributor(request)
    y = str(body.get("year", ""))
    c = str(body.get("country", ""))
    e = str(body.get("event", ""))
    rows = db.connect().execute(
        "SELECT id, owner FROM photos WHERE coalesce(album_year,'—')=? "
        "AND coalesce(country,'—')=? AND coalesce(event,'—')=?", (y, c, e)).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="no such album")
    if not ident.is_admin and any((r["owner"] or "") != ident.user for r in rows):
        raise HTTPException(status_code=403, detail="that is not your album")
    return register.remove([r["id"] for r in rows])


@app.post("/api/albums/journey")
def api_album_journey(request: Request, body: dict = Body(...)):
    """Set the departure and the means of transport for the journey (or empty
    = gone). ⚠ Looks the departure up (a network request) -- hence an
    admin/owner path."""
    ident = _contributor(request)
    y = str(body.get("year", ""))
    c = str(body.get("country", ""))
    e = str(body.get("event", ""))
    if not ident.is_admin:
        rows = db.connect().execute(
            "SELECT owner FROM photos WHERE coalesce(album_year,'—')=? "
            "AND coalesce(country,'—')=? AND coalesce(event,'—')=?", (y, c, e)).fetchall()
        if not rows or any((r["owner"] or "") != ident.user for r in rows):
            raise HTTPException(status_code=403, detail="that is not your album")
    legs = body.get("legs") if isinstance(body.get("legs"), list) else None
    # ⚠ `multi` stays `None` when it is not sent -- then, as before, it simply
    #   counts whether there are any legs. That way an old client does not break.
    multi = body.get("multi")
    multi = bool(multi) if isinstance(multi, bool) else None
    return journey.set_journey(y, c, e, str(body.get("departure", "")),
                               str(body.get("transport", "car")),
                               legs=legs, multi=multi)


@app.post("/api/albums/undo")
def api_album_undo(request: Request, body: dict = Body(...)):
    """Undo the last album rebuild. The batch comes out of the answer to
    `edit`."""
    ident = _contributor(request)
    batch = str(body.get("batch", ""))
    if not ident.is_admin:
        # Ownership is checked on the CURRENT album (where the photographs are now).
        import json as _j
        row = db.connect().execute(
            "SELECT meta FROM moves WHERE batch_id=? AND undone_at IS NULL",
            (batch,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="nothing to undo")
        new = (_j.loads(row["meta"] or "{}")).get("new", {})
        y, c, e = str(new.get("year", "")), str(new.get("country", "")), str(new.get("event", ""))
        owners = db.connect().execute(
            "SELECT owner FROM photos WHERE coalesce(album_year,'—')=? "
            "AND coalesce(country,'—')=? AND coalesce(event,'—')=?", (y, c, e)).fetchall()
        if not owners or any((r["owner"] or "") != ident.user for r in owners):
            raise HTTPException(status_code=403, detail="that is not your album")
    try:
        return album.undo(batch)
    except album.AlbumBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/admin/album/{year}/{country}/{event}", response_class=HTMLResponse)
def page_register(request: Request, year: str, country: str, event: str,
                  page: int = 1, show: str = "all"):
    """The register: pick photographs and rotate, hide or take them off the site.

    ⚠ A contributor sees ONLY their own photographs from that album here."""
    ident = _contributor(request)
    f = {"year": year, "country": country, "event": event}
    if not ident.is_admin:
        f["owner"] = ident.user
    res = gallery.list_photos(f, page, include_hidden=(show != "site"),
                              only_hidden=(show == "hidden"))
    return _page(request, "register.html", res=res,
                 heading=gallery.album_title(year, event),
                 year=year, country=country, event=event, show=show,
                 here={"year": year, "event": event},
                 base=f"/admin/album/{year}/{country}/{event}?show={show}&")


@app.post("/api/photos/bulk")
def api_bulk(request: Request, body: dict = Body(...)):
    """An action on a selection. `remove` NEVER deletes an original.

    ⚠ An admin may act on any photograph. A contributor ONLY on the ones they
    uploaded themselves -- so the selection is filtered down to their own. If
    nothing is left afterwards it is a 403: they should not have reached
    somebody else's photographs at all."""
    ident = _contributor(request)
    ids = _owns(ident, body.get("ids"))
    action = str(body.get("action", ""))
    if not (body.get("ids") or []):
        raise HTTPException(status_code=400, detail="no photographs selected")
    if not ids:
        raise HTTPException(status_code=403,
                            detail="those are not your photographs")
    if action == "hide":
        return register.set_hidden(ids, True)
    if action == "show":
        return register.set_hidden(ids, False)
    if action == "rotate_left":
        return register.rotate(ids, 3)
    if action == "rotate_right":
        return register.rotate(ids, 1)
    if action == "remove":
        return register.remove(ids)
    if action == "cover":
        # A cover is ONE photograph -- the first of the selection.
        try:
            return register.set_cover(ids[0])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")


# ---------------------------------------------------------------------------
#  Etapp 9 -- Tags, Persounen a Sammlungen
# ---------------------------------------------------------------------------
@app.get("/admin/tag", response_class=HTMLResponse)
def page_tag(request: Request, page: int = 1, year: str = "", country: str = "",
             event: str = "", person: str = "", tag: str = "", todo: int = 0):
    """The tagging machine. Its purpose: 200 photographs in one sitting.

    It is not pretty, it is fast -- one grid, a list that is always there, and
    the keys 1-9.
    """
    _admin(request)
    f = {k: v for k, v in
         {"year": year, "country": country, "event": event,
          "person": person, "tag": tag}.items() if v}
    if todo:
        f["untagged"] = True
    res = gallery.list_photos(f, page, page_size=120, include_hidden=True)
    ids = [p["id"] for p in res["photos"]]
    return _page(request, "tag.html", res=res,
                 people_on=tagging.of_photos("person", ids),
                 tags_on=tagging.of_photos("tag", ids),
                 sets_on=collections.of_photos(ids),
                 recent=tagging.recent_people(),
                 people=tagging.all_of("person"), tags=tagging.all_of("tag"),
                 sets=collections.all_of(), facets=gallery.facets(),
                 tagpick=tagging.tag_choices(),
                 peoplepick=tagging.person_choices(),
                 sel={"year": year, "country": country, "event": event,
                      "person": person, "tag": tag, "todo": todo},
                 todo_total=gallery.list_photos({"untagged": True}, 1, 1)["total"],
                 base_tag="/admin/tag?" + "&".join(
                     f"{k}={v}" for k, v in
                     {"year": year, "country": country, "event": event,
                      "person": person, "tag": tag,
                      "todo": todo or ""}.items() if v) + "&")


@app.post("/api/mark")
def api_mark(request: Request, body: dict = Body(...)):
    """Put a tag or a person on a selection, or take it off."""
    _admin(request)
    ids = [int(i) for i in (body.get("ids") or [])]
    alb = body.get("album") or {}
    if not ids and alb:
        # A whole album at once -- from the workshop. The ids are looked up
        # here and not in the browser: sending a list of 63 numbers through a
        # request is unnecessary, and it would be stale the moment anything
        # changes.
        ids = [r["id"] for r in db.connect().execute(
            "SELECT id FROM photos WHERE coalesce(album_year,'—')=? "
            "AND coalesce(country,'—')=? AND coalesce(event,'—')=?",
            (str(alb.get("year", "")), str(alb.get("country", "")),
             str(alb.get("event", ""))))]
    try:
        # `names` (a list, or text with commas) or `name` (a single one).
        name_list = body.get("names")
        if name_list is None:
            name_list = body.get("name")
        return tagging.assign_many(str(body.get("kind", "person")), name_list, ids,
                                   on=bool(body.get("on", True)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/names")
def api_names(request: Request, body: dict = Body(...)):
    """Create, rename or remove a name."""
    _admin(request)
    kind_ = str(body.get("kind", "person"))
    action = str(body.get("action", ""))
    try:
        if action == "new":
            return {"id": tagging.ensure(kind_, str(body.get("name", "")))}
        if action == "rename":
            return tagging.rename(kind_, int(body["id"]), str(body.get("name", "")))
        if action == "forget":
            return tagging.forget(kind_, int(body["id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")


@app.get("/admin/collections", response_class=HTMLResponse)
def page_collections(request: Request):
    ident = _contributor(request)
    mine = None if ident.is_admin else ident.user
    return _page(request, "collections.html", sets=collections.all_of(mine),
                 is_admin=ident.is_admin)


@app.post("/api/collections")
def api_collections(request: Request, body: dict = Body(...)):
    """Collections: create, rename, add and remove photographs, cover, delete.

    ⚠ A contributor may only touch THEIR OWN collections. For a new collection
    they may only take in THEIR OWN photographs -- otherwise a protected
    photograph belonging to somebody else would be handed out through a
    collection."""
    ident = _contributor(request)
    action = str(body.get("action", ""))
    ids = [int(i) for i in (body.get("ids") or [])]

    def _mine(cid):
        if not ident.is_admin and collections.owned_by(int(cid)) != ident.user:
            raise HTTPException(status_code=403, detail="that is not your collection")

    def _originals(photo_ids):
        if ident.is_admin:
            return photo_ids
        keep = _owns(ident, photo_ids)
        if photo_ids and not keep:
            raise HTTPException(status_code=403, detail="those are not your photographs")
        return keep

    try:
        if action == "new":
            return collections.create(str(body.get("title", "")), _originals(ids),
                                      owner=None if ident.is_admin else ident.user)
        if action == "rename":
            _mine(body["id"])
            return collections.rename(int(body["id"]), str(body.get("title", "")))
        if action == "add":
            _mine(body["id"])
            return collections.add(int(body["id"]), _originals(ids))
        if action == "drop":
            _mine(body["id"])
            return collections.drop(int(body["id"]), ids)
        if action == "cover":
            _mine(body["id"])
            return collections.set_cover(int(body["id"]), int(body["photo_id"]))
        if action == "remove":
            _mine(body["id"])
            return collections.remove(int(body["id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")


@app.get("/c/{slug}", response_class=HTMLResponse)
def page_collection(request: Request, slug: str):
    """A collection, as the family sees it."""
    st = collections.by_slug(slug)
    if st is None:
        raise HTTPException(status_code=404, detail="not found")
    photos = collections.photos_of(st["id"], viewer=security.identify(request))
    return _page(request, "collection.html", set=st,
                 res={"photos": photos, "total": len(photos),
                      "page": 1, "pages": 1, "page_size": len(photos) or 1},
                 heading=st["title"])


@app.get("/tag/{name}", response_class=HTMLResponse)
def page_tagged(request: Request, name: str, page: int = 1):
    res = gallery.list_photos({"tag": name}, page,
                              viewer=security.identify(request))
    return _page(request, "album.html", res=res, heading=name,
                 subplate=f"{res['total']} plate" + ("" if res["total"] == 1 else "s"),
                 submeta=["tag"], here={"tag": name},
                 base=f"/tag/{name}?")


@app.get("/person/{name}", response_class=HTMLResponse)
def page_person(request: Request, name: str, page: int = 1):
    res = gallery.list_photos({"person": name}, page,
                              viewer=security.identify(request))
    return _page(request, "album.html", res=res, heading=name,
                 subplate=f"{res['total']} plate" + ("" if res["total"] == 1 else "s"),
                 submeta=["photographed with"], here={"person": name},
                 base=f"/person/{name}?")


# ---------------------------------------------------------------------------
#  Settings, members and the album permissions
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
#  Signing in (app/auth.py). Only for `FAMILY_AUTH=local` -- in proxy mode the
#  identity proxy does this, and these paths do not exist.
# ---------------------------------------------------------------------------
def _sso_link() -> str:
    """Where the "sign in with SSO" button points -- or empty."""
    return os.environ.get("FAMILY_SSO_START", "") if config.AUTH_PROXY else ""


def _https(request: Request) -> bool:
    """Is this request running over https?

    ⚠ Three ways, because one is not enough: the address in the configuration
    (whoever has it wrong would otherwise get a cookie without `Secure`), the
    scheme of the request, and the proxy's header. `X-Forwarded-Proto` alone
    would be forgeable -- but here it only ever makes the protection stricter,
    never looser.
    """
    # ⚠ NOT by `FAMILY_SITE_URL`. That was the first version, and on a test
    #   installation it broke sign-in exactly like this: the address said
    #   https, the request came over http, the cookie got `Secure` -- and the
    #   browser threw it away at once. You were "signed in" and still outside.
    if request.url.scheme == "https":
        return True
    if (request.headers.get("x-forwarded-proto") or "").lower() == "https":
        return True
    return config.COOKIE_SECURE


def _set_session(request, resp, cookie: str):
    """⚠ `HttpOnly` (no script reaches it), `SameSite=Lax` (a foreign page
    cannot fire a request with your cookie) and `Secure`, as soon as the site
    runs over https."""
    resp.set_cookie(config.SESSION_COOKIE, cookie, max_age=config.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=_https(request), path="/")
    return resp


@app.get("/setup", response_class=HTMLResponse)
def page_setup(request: Request, error: str = ""):
    """First start: there is no account yet. ⚠ Once there is one this path is
    SHUT -- otherwise anyone could add a second admin to a running site."""
    if not config.AUTH_LOCAL:
        raise HTTPException(status_code=404, detail="not found")
    if auth.has_local_users():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {
        "site_title": config.SITE_TITLE, "static_ver": _static_ver(), "error": error})


@app.post("/setup")
async def do_setup(request: Request):
    if not config.AUTH_LOCAL or auth.has_local_users():
        return RedirectResponse("/login", status_code=303)
    f = await request.form()
    pw, pw2 = str(f.get("password", "")), str(f.get("password2", ""))
    if pw != pw2:
        return RedirectResponse("/setup?error=The+two+passwords+are+not+the+same",
                                status_code=303)
    try:
        # The group: the BIGGEST one from the configuration. That way the first
        # account has exactly the rights the site already knows as an admin's.
        admin_group = sorted(config.ADMIN_GROUPS)[0] if config.ADMIN_GROUPS else "admin"
        auth.create_user(str(f.get("username", "")), pw,
                         str(f.get("display_name", "")), groups=[admin_group])
    except ValueError as exc:
        from urllib.parse import quote
        return RedirectResponse(f"/setup?error={quote(str(exc))}", status_code=303)
    cookie = auth.new_session(str(f.get("username", "")).strip().lower(),
                              request.headers.get("user-agent", ""))
    return _set_session(request, RedirectResponse("/", status_code=303), cookie)


@app.get("/login", response_class=HTMLResponse)
def page_login(request: Request, next: str = "/", error: str = ""):
    # ⚠ Without local sign-in this page does NOT exist. Otherwise an
    #   SSO installation would show a password box nobody can type anything
    #   into -- and that looks like a fault in the site.
    if not config.AUTH_LOCAL:
        raise HTTPException(status_code=404, detail="not found")
    if not auth.has_local_users():
        return RedirectResponse("/setup", status_code=303)
    if security.identify(request).is_viewer:
        return RedirectResponse(next or "/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
        "site_title": config.SITE_TITLE, "static_ver": _static_ver(),
        "next": next or "/", "error": error, "sso": _sso_link()})


@app.post("/login")
async def do_login(request: Request):
    f = await request.form()
    goal_ = str(f.get("next", "/")) or "/"
    # ⚠ Only back to our own site: a "next" like `//example.com` would turn the
    #   site into a redirect for a foreign address.
    if not goal_.startswith("/") or goal_.startswith("//"):
        goal_ = "/"
    from urllib.parse import quote
    ip = security.client_ip(request)
    if auth.blocked(ip):
        # ⚠ Not a word about whether the account or the password was wrong, and
        #   not about how many tries are left. Whoever works through a list gets
        #   no feedback that helps them.
        return RedirectResponse(
            f"/login?next={quote(goal_, safe='/?=&')}"
            "&error=Too+many+tries.+Wait+a+few+minutes.", status_code=303)
    user = auth.check(str(f.get("username", "")), str(f.get("password", "")))
    if not user:
        auth.note_fail(ip)
        return RedirectResponse(
            f"/login?next={quote(goal_, safe='/?=&')}&error=That+did+not+work",
            status_code=303)
    auth.note_ok(ip)
    cookie = auth.new_session(user, request.headers.get("user-agent", ""))
    return _set_session(request, RedirectResponse(goal_, status_code=303), cookie)


@app.get("/logout")
@app.post("/logout")
def do_logout(request: Request):
    auth.end_session(request.cookies.get(config.SESSION_COOKIE, ""))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(config.SESSION_COOKIE, path="/")
    return resp


@app.get("/sw.js")
def service_worker():
    """The service worker.

    ⚠ It MUST sit at the root path. A service worker is only responsible for
    the path it is served under -- from `/static/sw.js` it would see nothing of
    the site. The file itself still lives in the `static/` folder, where the
    other scripts are.
    """
    return FileResponse(config.STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/app", response_class=HTMLResponse)
@app.get("/app/pair", response_class=HTMLResponse)
def page_devices(request: Request):
    """The user's devices: show a QR code, list them, throw one out.

    ⚠ For EVERY user, not only the admin -- an aunt who just wants to look
    needs a device too. It changes nothing about the rights: the app sees
    exactly what that user sees on the site.

    `/app/pair` is the same page: it is where you land if you scan the QR code
    with the ordinary camera. The code sits in the fragment (`#c=…`) and never
    reaches the server -- the page shows it so it can be typed into the app.
    """
    ident = security.identify(request)
    return _page(request, "app.html", user=ident.user)


@app.get("/admin/settings", response_class=HTMLResponse)
def page_settings(request: Request):
    """What the site knows about signing in -- and what there is."""
    ident = _admin(request)
    return _page(request, "settings.html",
                 members=members.all_of(),
                 pickable=members.pickable(),
                 groups=members.group_choices(),
                 admins=members.admin_names(),
                 authentik={
                     "configured": members.configured(),
                     "url": config.AUTHENTIK_URL or "—",
                     "token_file": config.AUTHENTIK_TOKEN_FILE or "—",
                     "refreshed": db.get_state("members_refreshed") or "never",
                     "only": sorted(members.relevant_groups()),
                 },
                 roles={"admin": sorted(config.ADMIN_GROUPS),
                         "viewer": sorted(config.VIEWER_GROUPS)},
                 seen={"user": ident.user, "groups": sorted(ident.groups),
                       "email": ident.email, "admin": ident.is_admin},
                 acls=acl.all_acls(),
                 tagpick=tagging.tag_choices(),
                 peoplepick=tagging.person_choices(),
                 alb_rows=album.list_all())


# ---------------------------------------------------------------------------
#  Apparater: d'iPhone-/iPad-App (app/devices.py)
# ---------------------------------------------------------------------------
@app.get("/api/app/authz")
def api_app_authz(request: Request):
    """The guard for the app. **nginx** calls this as an `auth_request`.

    ⚠ This path is the mirror image of the identity proxy's outpost: it
    answers 200 with the same headers (`X-authentik-username` / `-groups`),
    and nginx passes them on to the app. So the rest of the program does not
    know the difference between a browser and the app **at all** -- every
    route, every ACL check and every role condition stays as it is.

    ⚠ A 401 here makes nginx shut the door. No reason is given away.
    """
    head = request.headers.get("authorization", "")
    token = head[7:].strip() if head[:7].lower() == "bearer " else ""
    user = devices.identify(token)
    if not user:
        raise HTTPException(status_code=401, detail="no")
    row = db.connect().execute(
        "SELECT display_name, email, groups_json FROM members WHERE username=?",
        (user,)).fetchone()
    try:
        groups = json.loads(row["groups_json"]) if row and row["groups_json"] else []
    except (ValueError, TypeError):
        groups = []
    devices.touch(token, security.client_ip(request))
    # ⚠ `|` as the separator -- exactly as the outpost sends it (security.py
    # splits on `,` and `|`). A group name with a comma would become two groups.
    return Response(status_code=200, headers={
        "X-authentik-username": user,
        "X-authentik-groups": "|".join(str(g) for g in groups),
        "X-authentik-email": (row["email"] if row else "") or "",
    })


@app.post("/api/app/pair")
def api_app_pair(request: Request, body: dict = Body(...)):
    """Trade the pairing code for a token. This is the ONLY request from the
    app that works without a token -- it is locked by the code itself (five
    minutes, one use, and a rate limit in nginx)."""
    out = devices.redeem(str(body.get("code", "")), str(body.get("name", "")),
                         security.client_ip(request))
    if out is None:
        raise HTTPException(status_code=401, detail="that code is not valid any more")
    return out


@app.get("/api/app/me")
def api_app_me(request: Request):
    """Who am I and what may I -- the first thing the app asks."""
    ident = security.identify(request)
    return {"user": ident.user, "email": ident.email, "groups": list(ident.groups),
            "may": {"view": ident.is_viewer, "upload": ident.is_contributor,
                    "share": ident.is_contributor, "admin": ident.is_admin},
            "site": config.SITE_TITLE, "sizes": config.DERIVATIVE_WIDTHS}


@app.post("/api/devices/pair")
def api_devices_pair(request: Request):
    """A new pairing code plus its QR code. Comes from the browser, so signed in."""
    ident = security.identify(request)
    if not ident.is_viewer:
        raise HTTPException(status_code=403, detail="no")
    out = devices.new_pairing(ident.user)
    url = devices.pair_url(out["code"])
    return {**out, "url": url, "svg": devices.qr_svg(url)}


@app.get("/api/devices")
def api_devices_list(request: Request):
    ident = security.identify(request)
    if not ident.is_viewer:
        raise HTTPException(status_code=403, detail="no")
    return {"devices": devices.list_for(ident.user, all_users=ident.is_admin),
            "me": ident.user}


@app.post("/api/devices/revoke")
def api_devices_revoke(request: Request, body: dict = Body(...)):
    ident = security.identify(request)
    if not ident.is_viewer:
        raise HTTPException(status_code=403, detail="no")
    ok = devices.revoke(int(body.get("id") or 0), ident.user, ident.is_admin)
    if not ok:
        raise HTTPException(status_code=404, detail="that device is not yours")
    return {"ok": True}


@app.post("/api/members/refresh")
def api_members_refresh(request: Request):
    """Fetch the list from the directory. Only on a button press."""
    _admin(request)
    return members.refresh()


@app.post("/api/albums/audience")
def api_album_audience(request: Request, body: dict = Body(...)):
    """Who sees an album. An empty list = **the admin only**."""
    ident = _contributor(request)
    y, c, e = str(body.get("year", "")), str(body.get("country", "")), str(body.get("event", ""))
    if not ident.is_admin:
        rows = db.connect().execute(
            "SELECT owner FROM photos WHERE coalesce(album_year,'—')=? "
            "AND coalesce(country,'—')=? AND coalesce(event,'—')=?", (y, c, e)).fetchall()
        if not rows or any((r["owner"] or "") != ident.user for r in rows):
            raise HTTPException(status_code=403, detail="that is not your album")
    try:
        return acl.set_audience(
            str(body.get("year", "")), str(body.get("country", "")),
            str(body.get("event", "")), body.get("audience") or [])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/albums/share")
def api_album_share(request: Request, body: dict = Body(...)):
    """Make a share link DIRECTLY out of an album -- without building a
    collection first. Creates a collection from the album's photographs and
    shares that.

    ⚠ An admin shares the whole album; an account holder ONLY their own
    photographs out of it -- otherwise they would hand out somebody else's
    photographs through a link."""
    ident = _contributor(request)
    year = str(body.get("year", ""))
    country = str(body.get("country", ""))
    event = str(body.get("event", ""))
    # ⚠ The rule: whoever may LOOK at an album may also SHARE it -- and shares
    # the whole album (the same photographs they see). The may_see check is
    # therefore the only hurdle.
    if not acl.may_see(ident, year, country, event):
        raise HTTPException(status_code=404, detail="not found")
    rows = db.connect().execute(
        "SELECT id FROM photos WHERE state='ok' AND hidden=0 "
        "AND album_year=? AND country=? AND event=?", (year, country, event)).fetchall()
    ids = [r["id"] for r in rows]
    if not ids:
        raise HTTPException(status_code=404, detail="not found")
    # View limit: a user's link works once (a user canNOT change that).
    # The admin sets it themselves (default: unlimited).
    max_views = (body.get("max_views") if ident.is_admin else 1)
    try:
        coll = collections.create(gallery.album_title(year, event), ids,
                                  owner=None if ident.is_admin else ident.user)
        d = shares.create(coll["id"], days=int(body.get("days", shares.DEFAULT_DAYS)),
                          allow_download=bool(body.get("allow_download", True)),
                          keep_gps=bool(body.get("keep_gps", False)),
                          owner=None if ident.is_admin else ident.user,
                          max_views=max_views)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _FRESH[ident.user] = d
    return d


# ---------------------------------------------------------------------------
#  Etapp 10 -- Deel-Links
# ---------------------------------------------------------------------------
@app.get("/admin/shares", response_class=HTMLResponse)
def page_shares(request: Request):
    ident = _contributor(request)
    mine = None if ident.is_admin else ident.user
    return _page(request, "shares.html", links=shares.all_of(mine),
                 sets=collections.all_of(mine),
                 is_admin=ident.is_admin,
                 quarantine=guests.waiting() if ident.is_admin else [],
                 fresh=_FRESH.pop(ident.user, None))


# ⚠ The password is shown ONCE. It is not in the database and it does not
# travel in a GET address -- so it is held here briefly, until the page is
# loaded once more after the link was created.
_FRESH = {}


@app.post("/api/shares")
def api_shares(request: Request, body: dict = Body(...)):
    ident = _contributor(request)
    action = str(body.get("action", ""))

    def _mine_collection(cid):
        # ⚠ A contributor may only share a collection that belongs to them.
        if not ident.is_admin and collections.owned_by(int(cid)) != ident.user:
            raise HTTPException(status_code=403, detail="that is not your collection")

    def _mine_share(sid):
        if not ident.is_admin and shares.owner_of(int(sid)) != ident.user:
            raise HTTPException(status_code=403, detail="that is not your link")

    try:
        if action == "new":
            _mine_collection(body["collection_id"])
            # View-Limit: e Benotzer-Link geet just 1×, den Admin setzt et selwer.
            max_views = (body.get("max_views") if ident.is_admin else 1)
            d = shares.create(int(body["collection_id"]),
                              days=int(body.get("days", shares.DEFAULT_DAYS)),
                              allow_download=bool(body.get("allow_download", True)),
                              allow_upload=bool(body.get("allow_upload", False)),
                              keep_gps=bool(body.get("keep_gps", False)),
                              owner=None if ident.is_admin else ident.user,
                              max_views=max_views)
            _FRESH[ident.user] = d
            return d
        if action == "views":
            # Change or switch off the view limit of an existing link.
            # ⚠ ONLY the admin may loosen it; a user stays at 1.
            _mine_share(body["id"])
            if not ident.is_admin:
                raise HTTPException(status_code=403,
                                    detail="only an admin can change the open limit")
            return shares.set_views(int(body["id"]), body.get("max_views"))
        if action == "password":
            _mine_share(body["id"])
            d = shares.new_password(int(body["id"]))
            _FRESH[ident.user] = d
            return d
        if action == "revoke":
            _mine_share(body["id"])
            return shares.revoke(int(body["id"]))
        # The guests' quarantine belongs to the admin -- foreign files land
        # there, and approving one gives it access to the library.
        if action == "accept":
            _admin(request)
            return guests.accept_any([str(i) for i in (body.get("ids") or [])])
        if action == "reject":
            _admin(request)
            return guests.reject_any([str(i) for i in (body.get("ids") or [])])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")


def _share_or_404(token: str, request: Request):
    """Open the link -- or a page that gives nothing away."""
    st = shares.state(token)
    if not st["ok"] and st["reason"] in ("unknown",):
        raise HTTPException(status_code=404, detail="not found")
    return st


def _unlocked(token: str, request: Request) -> bool:
    return shares.valid_cookie(token, request.cookies.get(shares.cookie_name(token), ""))


@app.get("/s/{token}", response_class=HTMLResponse)
def page_share(request: Request, token: str):
    st = _share_or_404(token, request)
    if not st["ok"]:
        return _share_page(request, "share_closed.html", reason=st["reason"],
                           row=st.get("row"), status=410 if st["reason"] != "locked" else 429)
    row = st["row"]
    if not _unlocked(token, request):
        return _share_page(request, "share_locked.html", token=token, row=row)
    # ⚠ View limit: a user's link works once. The counter counts ONLY the page
    # view (share_hits), not the images -- otherwise the first look would stop
    # itself with its own thumbnails. Checked BEFORE `note_hit`.
    if shares.used_up(row):
        return _share_page(request, "share_closed.html", reason="used up",
                           row=row, status=410)
    shares.note_hit(row["id"], request.headers.get("user-agent", ""))
    photos = collections.photos_of(row["album_id"], viewer=None)
    return _share_page(request, "share.html", token=token, row=row,
                       res={"photos": photos, "total": len(photos),
                            "page": 1, "pages": 1, "page_size": len(photos) or 1})


@app.post("/s/{token}")
def post_share(request: Request, token: str, password: str = Form("")):
    st = _share_or_404(token, request)
    res = shares.check_password(token, password)
    if not res["ok"]:
        if res["reason"] in ("expired", "revoked"):
            return _share_page(request, "share_closed.html", reason=res["reason"],
                               row=res.get("row"), status=410)
        if res["reason"] == "locked":
            return _share_page(request, "share_closed.html", reason="locked",
                               row=res.get("row"), status=429)
        return _share_page(request, "share_locked.html", token=token,
                           row=st.get("row"), wrong=True, status=401)
    row = res["row"]
    # The cookie never lives longer than the link itself.
    until_ = min(row["expires_at"],
              (datetime.now() + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S"))
    r = RedirectResponse(f"/s/{token}", status_code=303)
    r.set_cookie(shares.cookie_name(token), shares.sign(token, until_),
                 path=f"/s/{token}", httponly=True, secure=True, samesite="lax",
                 max_age=12 * 3600)
    return r


@app.get("/s/{token}/all.zip")
def share_zip_all(request: Request, token: str):
    """The WHOLE album behind a share link as ONE zip (download all).

    ⚠ Same as the single download: everything is stripped (GPS, serial number,
    owner) unless the link explicitly says `keep_gps`. Only runs when
    downloading is allowed."""
    st = shares.state(token)
    if not st["ok"] or not _unlocked(token, request):
        raise HTTPException(status_code=404, detail="not found")
    row = st["row"]
    if not row["allow_download"]:
        raise HTTPException(status_code=404, detail="not found")
    photos = collections.photos_of(row["album_id"], viewer=None)
    if not photos:
        raise HTTPException(status_code=404, detail="not found")
    slug = db.connect().execute(
        "SELECT slug FROM albums WHERE id=?", (row["album_id"],)).fetchone()
    name = (slug["slug"] if slug else "album") or "album"
    zpath = guests.zip_all(photos, keep_gps=bool(row["keep_gps"]))
    from fastapi.responses import FileResponse as _FR
    return _FR(zpath, media_type="application/zip", filename=f"{name}.zip",
               headers=guests._HEADERS, background=guests._delete_later(zpath))


@app.get("/s/{token}/img/{photo_id}/{size}.{ext}")
def share_img(request: Request, token: str, photo_id: int, size: str, ext: str):
    """An image from a share link.

    ⚠ The `photo_id` is checked against the token's collection on EVERY
    request. Without that, an id from another link could be pushed through here."""
    st = shares.state(token)
    if not st["ok"] or not _unlocked(token, request):
        raise HTTPException(status_code=404, detail="not found")
    if photo_id not in shares.photo_ids(st["row"]["id"]):
        raise HTTPException(status_code=404, detail="not found")
    # A video in a shared collection: playable (like a thumbnail -- otherwise a
    # shared video could not be watched at all, because there is no smaller
    # step). The `allow_download` flag applies to the EXPLICIT download.
    if size == "video" and ext == "mp4":
        return serve.video(photo_id, admin=True)
    if size == "master" and ext == "jpg":
        if not st["row"]["allow_download"]:
            raise HTTPException(status_code=404, detail="not found")
        return guests.clean_master(photo_id, keep_gps=bool(st["row"]["keep_gps"]))
    try:
        width = int(size)
    except ValueError:
        raise HTTPException(status_code=404, detail="no such size")
    return serve.derivative(photo_id, width, ext, admin=True)


@app.post("/s/{token}/upload")
async def share_upload(request: Request, token: str):
    """A guest adds something -- into quarantine, never onto the site.

    ⚠ Two things here are deliberate:

    1. **No `File(...)` dependency.** That would have let FastAPI read the
       whole body BEFORE this function runs at all -- and with it, a stranger
       holding a token that does not exist could push a few hundred MB into
       memory just to get a 404. Now the link is checked first and read only
       afterwards.
    2. **`run_in_threadpool`.** `guests.receive` reads, hashes and calls
       exiftool three times -- all of it blocking. On the event loop a single
       odd file would have stopped the whole site for up to three minutes, for
       everybody.
    """
    st = shares.state(token)
    if not st["ok"] or not _unlocked(token, request):
        raise HTTPException(status_code=404, detail="not found")
    if not st["row"]["allow_upload"]:
        raise HTTPException(status_code=403, detail="uploads are off for this link")
    form = await request.form()
    fname = form.get("file")
    if fname is None or not hasattr(fname, "read"):
        raise HTTPException(status_code=400, detail="no file")
    try:
        return await run_in_threadpool(
            guests.receive_sync, st["row"], str(form.get("guest") or ""), fname)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/admin/upload", response_class=HTMLResponse)
def page_upload(request: Request):
    ident = _contributor(request)
    return _page(request, "upload.html", members=members.pickable(),
                 groups=members.group_choices(), me=ident.user,
                 is_admin=ident.is_admin)


@app.get("/api/photo/{photo_id}")
def api_photo(photo_id: int, request: Request):
    p = gallery.photo(photo_id, viewer=security.identify(request))
    if p is None:
        raise HTTPException(status_code=404, detail="not found")
    p.pop("origin_path", None)      # the original's path is the admin's business
    p.pop("master_source_path", None)
    who = security.identify(request)
    p["lqip"] = serve.lqip(photo_id, who.is_admin, who)
    return p


@app.get("/api/albums")
def api_albums(request: Request):
    """The album list as JSON -- for the app.

    ⚠ `/api/tree` is admin work (the paths on disk are in it). Here comes only
    what the viewer sees anyway: year, country, name, count, cover.
    """
    who = security.identify(request)
    return {"albums": gallery.albums(viewer=who)}


@app.get("/api/photos")
def api_photos(request: Request, page: int = 1, year: str = None, country: str = None,
               event: str = None, place: str = None, camera: str = None,
               kind: str = None, q: str = None):
    return gallery.list_photos(
        {"year": year, "country": country, "event": event, "place": place,
         "camera": camera, "kind": kind, "q": q}, page,
        viewer=security.identify(request))


@app.get("/api/facets")
def api_facets(request: Request):
    return gallery.facets(security.identify(request))


@app.get("/tiles/{z}/{x}/{y}.png")
def map_tile(z: int, x: int, y: int, request: Request):
    """D'Kacheln lafen duerch de Server. Kuck app/tiles.py firwat."""
    return tiles.tile(z, x, y)


@app.post("/api/geocode")
def api_geocode(request: Request):
    """Look up every place that has not been looked up yet, once."""
    _admin(request)
    return geo.resolve_all()


@app.get("/api/tiles/seed")
def api_tiles_plan(request: Request):
    """What a tile seed would load -- without loading anything."""
    _admin(request)
    return tileseed.plan()


@app.post("/api/tiles/seed")
def api_tiles_seed(request: Request):
    """Load the map tiles into the container in advance (runs as a job).

    To make the map work offline. First look the places up (geocode), then
    seed, and then set FAMILY_TILE_OFFLINE=1."""
    _admin(request)
    return {"job": enqueue("tileseed"), **tileseed.plan()}


@app.get("/photos/{photo_id}/{size}.{ext}")
def photo_file(photo_id: int, size: str, ext: str, request: Request):
    # Only the admin sees a hidden photograph -- the register needs that.
    who = security.identify(request)
    admin = who.is_admin
    if size == "master" and ext == "jpg":
        return serve.master(photo_id, admin, who)
    if size == "video" and ext == "mp4":
        return serve.video(photo_id, admin, who)
    try:
        width = int(size)
    except ValueError:
        raise HTTPException(status_code=404, detail="no such size")
    return serve.derivative(photo_id, width, ext, admin, who)
