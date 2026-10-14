"""Routers, one per job. app.py includes them all; the auth middleware in
app.py covers every one of them by default, so a router added here later is
protected without anyone remembering to decorate it."""

from fastapi import HTTPException, Request

import auth
import config


def current_user(request: Request):
    return auth.read_cookie(request.cookies.get(auth.COOKIE))


def actor(request: Request):
    if not config.CFG.get("auth_enabled", True):
        return "local"
    return current_user(request) or ""


def require_owner(request: Request):
    if not config.CFG.get("auth_enabled", True):
        return "local"
    who = current_user(request)
    if not who:
        raise HTTPException(401, "not logged in")
    return who


ROUTERS = ["auth_routes", "system", "accounts", "transactions", "categories", "payees", "rules",
           "transfers", "imports", "attachments", "business", "tax", "reports", "investments", "networth", "recurring", "budgets", "coinbase", "collectr", "credit"]


def include_routers(app):
    import importlib
    for name in ROUTERS:
        app.include_router(importlib.import_module("api." + name).router)
