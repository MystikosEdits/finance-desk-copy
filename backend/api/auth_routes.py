"""Login, logout, and the one-time /setup."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import auth
import config
from api import current_user

router = APIRouter(prefix="/api")


class LoginReq(BaseModel):
    name: str
    password: str


def is_local(request: Request):
    return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost")


def may_setup(request: Request):
    """First-run only, and only from this machine.

    Two conditions, both required: NO users exist yet, and the request came
    from localhost. It switches itself off the moment an account exists. The
    other half is in app.py: while no users exist the server binds 127.0.0.1
    whatever config.json says, so this window is unreachable from the network
    even in the instant before the first account. From the laptop the way in
    is an SSH tunnel:  ssh -L 8020:127.0.0.1:8020 you@your-server
    """
    return is_local(request) and not auth.any_users()


def _set_cookie(response: Response, name):
    response.set_cookie(auth.COOKIE, auth.make_cookie(name), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", path="/")


@router.get("/setup")
def setup_status(request: Request):
    return {"allowed": may_setup(request), "users_exist": auth.any_users()}


@router.post("/setup")
def setup(req: LoginReq, request: Request, response: Response):
    # Re-checked here, not just on the GET: this is the request that writes.
    if not may_setup(request):
        raise HTTPException(403, "setup is closed - an account already exists, or you are not on localhost")
    name = req.name.strip()
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "letters, numbers, - and _ only")
    if len(req.password) < 8:
        raise HTTPException(400, "use at least 8 characters")
    auth.set_password(name, req.password, "owner")
    _set_cookie(response, name)
    return {"ok": True, "name": name,
            "note": "account created. Restart the FinanceDesk task so the server opens to the tailnet."}


@router.post("/login")
def login(req: LoginReq, response: Response):
    name = req.name.strip()
    if not auth.check_password(name, req.password):
        raise HTTPException(401, "wrong name or password")
    _set_cookie(response, name)
    return {"ok": True, "name": name}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    if not config.CFG.get("auth_enabled", True):
        return {"name": "local", "auth": False}
    who = current_user(request)
    if not who:
        raise HTTPException(401, "not logged in")
    return {"name": who, "auth": True}
