"""Who is using the desk - passwords, sessions, and the users file.

    python auth.py add <name>          add an owner (prompts for the password)
    python auth.py list                who exists
    python auth.py passwd <name>       change a password
    python auth.py logout-everyone     rotate the secret, killing every session

This is Reaction Desk's auth.py with the roles collapsed to one. The reasoning
carries over unchanged and is worth keeping in front of whoever edits this.

WHY STDLIB ONLY
---------------
`hashlib.scrypt` is a real password KDF and ships with Python, so passlib or
bcrypt would buy nothing here but another thing to install.

WHY A SIGNED COOKIE AND NOT A SESSION STORE
-------------------------------------------
The cookie carries `user|expiry` plus an HMAC of both, signed with a secret in
data/secret.key. Nothing is stored server-side, which means A RESTART DOES NOT
LOG ANYONE OUT - and this server gets restarted every time its code changes.
The trade is that a single session cannot be revoked before it expires;
`logout-everyone` rotates the secret and invalidates the lot.

WHY A COOKIE AND NOT A BEARER TOKEN
-----------------------------------
<img src="/api/attachments/12"> is issued by the browser and cannot set an
Authorization header. A bearer token would leave every receipt preview broken
while the rest of the app looked fine. CSRF is closed by SameSite=Lax plus the
X-Requested-With header app.py demands on every non-GET API call.

WHY THE PASSWORD IS NEVER AN ARGUMENT
-------------------------------------
`add` and `passwd` prompt. A password on the command line lands in the shell
history and the process list. Note getpass BLOCKS where there is no real
console (a Claude Code session, a Scheduled Task) rather than failing - which
is why the first account is made in the browser at /setup instead.
"""

import base64
import getpass
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import threading
import time

import config

USERS_PATH = os.path.join(config.DATA, "users.json")
SECRET_PATH = os.path.join(config.DATA, "secret.key")

COOKIE = "fd_session"
SESSION_DAYS = 30
ROLES = ("owner",)

# n=2**14 keeps a login around a tenth of a second on the rig.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32

_lock = threading.Lock()


def secret():
    """The HMAC key, made on first use. Lives in data/, never in config.json."""
    with _lock:
        try:
            with io.open(SECRET_PATH, "rb") as fh:
                key = fh.read().strip()
            if key:
                return key
        except OSError:
            pass
        key = base64.urlsafe_b64encode(secrets.token_bytes(48))
        os.makedirs(config.DATA, exist_ok=True)
        with io.open(SECRET_PATH, "wb") as fh:
            fh.write(key)
        return key


def rotate_secret():
    with _lock:
        os.makedirs(config.DATA, exist_ok=True)
        with io.open(SECRET_PATH, "wb") as fh:
            fh.write(base64.urlsafe_b64encode(secrets.token_bytes(48)))


def load_users():
    try:
        with io.open(USERS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_users(users):
    os.makedirs(config.DATA, exist_ok=True)
    with io.open(USERS_PATH, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2)


def any_users():
    return bool(load_users())


def _hash(password, salt):
    return base64.b64encode(hashlib.scrypt(
        password.encode("utf-8"), salt=base64.b64decode(salt),
        n=_N, r=_R, p=_P, dklen=_DKLEN)).decode("ascii")


def set_password(name, password, role="owner"):
    users = load_users()
    salt = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    rec = users.get(name, {})
    rec["salt"] = salt
    rec["hash"] = _hash(password, salt)
    rec["role"] = role
    users[name] = rec
    save_users(users)
    return rec


def check_password(name, password):
    """True if the password is right. Constant-time on the hash compare."""
    rec = load_users().get(name)
    if not rec:
        # Hash anyway so a missing user takes the same time as a wrong
        # password - otherwise the response time says which names exist.
        _hash(password, base64.b64encode(b"x" * 16).decode("ascii"))
        return False
    return hmac.compare_digest(_hash(password, rec["salt"]), rec["hash"])


def make_cookie(name, days=SESSION_DAYS):
    exp = str(int(time.time() + days * 86400))
    body = "%s|%s" % (name, exp)
    sig = hmac.new(secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return "%s|%s" % (body, sig)


def read_cookie(value):
    """Return the username, or None. Any malformed value is simply not a login."""
    if not value:
        return None
    try:
        name, exp, sig = value.rsplit("|", 2)
    except ValueError:
        return None
    body = "%s|%s" % (name, exp)
    want = hmac.new(secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return None
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    if name not in load_users():
        return None
    return name


def _ask_password():
    a = getpass.getpass("password: ")
    if len(a) < 8:
        print("too short - use at least 8 characters")
        return None
    if a != getpass.getpass("again: "):
        print("they do not match")
        return None
    return a


def _cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "add" and len(sys.argv) >= 3:
        name = sys.argv[2]
        if name in load_users():
            print("%s already exists - use `passwd` to change it" % name)
            return 1
        pw = _ask_password()
        if not pw:
            return 1
        set_password(name, pw)
        print("added %s" % name)
        return 0
    if cmd == "passwd" and len(sys.argv) >= 3:
        name = sys.argv[2]
        if name not in load_users():
            print("no such user: %s" % name)
            return 1
        pw = _ask_password()
        if not pw:
            return 1
        set_password(name, pw)
        print("changed the password for %s" % name)
        return 0
    if cmd == "list":
        users = load_users()
        if not users:
            print("no users yet - make the first one in the browser at /setup")
            return 0
        for n in sorted(users):
            print("%-20s %s" % (n, users[n].get("role", "owner")))
        return 0
    if cmd == "logout-everyone":
        rotate_secret()
        print("secret rotated - every session is now invalid")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
