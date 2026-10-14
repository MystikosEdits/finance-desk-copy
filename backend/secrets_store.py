"""Small secrets (the SimpleFIN access URL) wrapped with Windows DPAPI.

WHY DPAPI AND NOT THE DATABASE
------------------------------
The database is backed up to OneDrive every night. A bank credential must not
ride along. DPAPI (CryptProtectData, user scope) needs no dependency and no
key file; the blob can only be opened by the Windows account that wrote it,
which is the account the FinanceDesk task runs as.

WHAT THAT MEANS IN PRACTICE
---------------------------
- The task must keep running as YOUR-PC\\you. Run it as SYSTEM or another
  user and unprotect fails with a bare Win32 error. Do not debug that: re-claim
  a SimpleFIN token from Settings.
- The blob is deliberately not portable. On a new machine you claim a new token.
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys

import config

_ENTROPY = b"finance-desk-v1"


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _call(fn, data):
    if sys.platform != "win32":
        raise RuntimeError("DPAPI is Windows-only")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    inp, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _BLOB()
    ok = getattr(crypt32, fn)(ctypes.byref(inp), None, ctypes.byref(ent), None, None,
                              0x01,  # CRYPTPROTECT_UI_FORBIDDEN: never pop a dialog from a service
                              ctypes.byref(out))
    if not ok:
        raise OSError("%s failed: Win32 error %d" % (fn, ctypes.GetLastError()))
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    return _call("CryptProtectData", data)


def unprotect(blob: bytes) -> bytes:
    return _call("CryptUnprotectData", blob)


def _path(name):
    return os.path.join(config.SECRETS_DIR, name + ".dpapi")


def set(name, value: str):
    os.makedirs(config.SECRETS_DIR, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(protect(value.encode("utf-8")))
    os.replace(tmp, _path(name))


def get(name):
    try:
        with open(_path(name), "rb") as fh:
            return unprotect(fh.read()).decode("utf-8")
    except FileNotFoundError:
        return None


def exists(name):
    return os.path.exists(_path(name))


def delete(name):
    try:
        os.remove(_path(name))
    except FileNotFoundError:
        pass
