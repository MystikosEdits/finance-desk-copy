"""Finance Desk - the server.

    .venv\\Scripts\\python.exe backend\\app.py          run (the FinanceDesk task does this)
    .venv\\Scripts\\python.exe backend\\app.py --check  integrity + backup verify, then exit

One process, one uvicorn worker, one scheduler thread. Every route lives in
api/*.py; this file is the middleware, the static frontend, and the startup
guard rails.

WHY THE MIDDLEWARE AND NOT A DECORATOR PER ROUTE
------------------------------------------------
One check over every request means a route added next month is protected by
default. The auth-free list is short and named.

WHY X-Requested-With
--------------------
The session is a cookie (so <img src="/api/attachments/12"> works). A cookie
is sent by the browser on any request, including one a hostile page makes.
SameSite=Lax stops the cross-site POST in every modern browser; requiring a
custom header on every non-GET API call stops it in all of them, because a
cross-site form cannot set one. Five lines for a second lock on money data.
"""

import contextlib
import os
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import auth
import backup
import config
import db
import log
import scheduler
from api import current_user, include_routers

logger = log.get("app")
BOOT_AT = time.time()

AUTH_FREE = ("/health", "/login", "/setup", "/api/login", "/api/setup")
AUTH_FREE_PREFIXES = ("/assets/", "/favicon")
CSRF_HEADER = "FinanceDesk"


def newest_backend_mtime():
    newest = 0.0
    for root, _dirs, files in os.walk(config.HERE):
        if "tests" in root or "__pycache__" in root:
            continue
        for f in files:
            if f.endswith((".py", ".sql", ".json")):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
    return newest


def register_jobs():
    cfg = config.CFG
    scheduler.register("backup", backup.run, [cfg["backup_time"]])
    scheduler.register("integrity", _integrity_job, ["03:00"])
    # sync / prices / networth / recurring register themselves here as the
    # modules land; keeping the list in one place means the Settings page
    # shows every job the server knows about.
    try:
        import sync
        scheduler.register("sync", sync.run_sync, cfg["sync_times"])
    except ImportError:
        pass
    try:
        import prices
        import networth
        scheduler.register("prices", prices.refresh_all, [cfg["prices_time"]])
        scheduler.register("networth", networth.snapshot_today, ["04:20"])
    except ImportError:
        pass
    try:
        import recurring
        scheduler.register("recurring", recurring.detect_and_store, ["05:00"])
    except ImportError:
        pass
    try:
        import coinbase
        # a few minutes after each bank pull, so both feeds land before a look
        scheduler.register("coinbase", coinbase.run_sync, [_shift(t, 5) for t in cfg["sync_times"]])
    except ImportError:
        pass
    try:
        import collectr_share
        scheduler.register("collectr", collectr_share.run_sync, ["04:10"])   # before the net worth snapshot
    except ImportError:
        pass


def _shift(hhmm, minutes):
    h, m = (int(x) for x in hhmm.split(":"))
    total = (h * 60 + m + minutes) % (24 * 60)
    return "%02d:%02d" % (total // 60, total % 60)


def _integrity_job():
    res = db.integrity_check()
    if not res["ok"]:
        raise RuntimeError("integrity problems: %s" % {k: v for k, v in res.items() if k != "ok"})


@contextlib.asynccontextmanager
async def lifespan(app):
    log.setup()
    config.ensure_dirs()
    applied = db.migrate()
    if applied:
        logger.info("applied migrations: %s", applied)
    register_jobs()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Finance Desk", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if config.CFG.get("auth_enabled", True):
        free = path in AUTH_FREE or path.startswith(AUTH_FREE_PREFIXES)
        if not free and not current_user(request):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "not logged in"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
    if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Requested-With") != CSRF_HEADER:
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
    return await call_next(request)


@app.get("/health")
def health():
    with db.read() as conn:
        last_sync = db.scalar(conn, "SELECT last_finished_at FROM jobs WHERE name = 'sync'")
        last_backup = db.scalar(conn, "SELECT last_finished_at FROM jobs WHERE name = 'backup' AND last_ok = 1")
        ok = db.scalar(conn, "SELECT 1")
    return {
        "ok": ok == 1,
        "db": True,
        "dist_built": os.path.exists(os.path.join(config.FRONTEND_DIST, "index.html")),
        "boot_at": BOOT_AT,
        "newest_backend_mtime": newest_backend_mtime(),
        "stale": newest_backend_mtime() > BOOT_AT + 1,
        "setup_mode": not auth.any_users(),
        "last_sync": last_sync,
        "last_backup": last_backup,
    }


include_routers(app)

# The built frontend. /assets is Vite's hashed output; everything that is not
# an API route gets index.html and the SPA router takes it from there.
_ASSETS = os.path.join(config.FRONTEND_DIST, "assets")
if os.path.isdir(_ASSETS):
    app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    index = os.path.join(config.FRONTEND_DIST, "index.html")
    candidate = os.path.join(config.FRONTEND_DIST, path)
    if path and os.path.isfile(candidate) and os.path.abspath(candidate).startswith(os.path.abspath(config.FRONTEND_DIST)):
        return FileResponse(candidate)
    return FileResponse(index)


def _check():
    log.setup()
    db.migrate()
    res = db.integrity_check()
    print("integrity:", "ok" if res["ok"] else res)
    print("backup verify:", backup.verify())
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_check())

    import uvicorn
    cfg = config.CFG
    host = cfg.get("host", "127.0.0.1")

    if not os.path.exists(os.path.join(config.FRONTEND_DIST, "index.html")):
        sys.exit("REFUSING TO START: frontend/dist/index.html is missing.\n"
                 "Build it first:  cd frontend && npm ci && npm run build   (deploy.ps1 does this)")

    # Refuse to serve the network with the door open. Binding beyond localhost
    # while auth is off is the one mistake that must be impossible.
    if host != "127.0.0.1" and not cfg.get("auth_enabled", True):
        sys.exit("REFUSING TO START: host is %s but auth_enabled is false.\n"
                 "Set auth_enabled true in config.json, or bind 127.0.0.1." % host)

    # No account yet: bind localhost no matter what config says, so /setup is
    # reachable only from this machine (or an SSH tunnel to it) and the tailnet
    # never sees an app with no login.
    if not auth.any_users():
        host = "127.0.0.1"
        print("SETUP MODE: no account exists yet, so this is bound to 127.0.0.1 only.")
        print("  From the laptop:  ssh -L 8020:127.0.0.1:8020 you@your-server")
        print("  then open        http://localhost:%d/setup" % cfg["port"])
        print("  and restart the FinanceDesk task afterwards to open it to the tailnet.")

    print("Finance Desk -> http://localhost:%d" % cfg["port"])
    if host != "127.0.0.1":
        print("             -> reachable on the network as %s (Tailscale only, by firewall rule)" % host)
    # access logs on: when the UI "does nothing", the first thing worth knowing
    # is whether the browser is reaching this process at all
    uvicorn.run(app, host=host, port=cfg["port"], log_level="info")
