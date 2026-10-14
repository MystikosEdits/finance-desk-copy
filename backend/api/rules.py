import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import db
import rules as R
from api import actor

router = APIRouter(prefix="/api/rules")


def _out(r):
    r = dict(r)
    r["match"] = json.loads(r.pop("match_json"))
    r["action"] = json.loads(r.pop("action_json"))
    return r


@router.get("")
def list_rules():
    with db.read() as conn:
        return [_out(r) for r in db.rows(conn, "SELECT * FROM rules ORDER BY priority, id")]


class RuleReq(BaseModel):
    name: str
    match: dict
    action: dict
    priority: int | None = 100
    active: int | None = 1
    stop: int | None = 1


def _validate(req):
    if not req.match:
        raise HTTPException(400, "a rule needs at least one match condition")
    if "regex" in req.match:
        import re
        try:
            re.compile(req.match["regex"])
        except re.error as e:
            raise HTTPException(400, "bad regex: %s" % e)
    if req.action.get("split"):
        total = sum(float(p.get("pct", 0)) for p in req.action["split"])
        if abs(total - 100) > 0.01:
            raise HTTPException(400, "split percentages must add to 100 (got %s)" % total)


@router.post("")
def create(req: RuleReq, request: Request):
    _validate(req)
    with db.write() as conn:
        cur = conn.execute("INSERT INTO rules (priority, name, active, stop, match_json, action_json) VALUES (?, ?, ?, ?, ?, ?)",
                           (req.priority or 100, req.name.strip(), 1 if req.active is None else req.active,
                            1 if req.stop is None else req.stop, json.dumps(req.match), json.dumps(req.action)))
        audit.log(conn, actor(request), "rule", cur.lastrowid, "create", None, req.model_dump())
        return _out(db.row(conn, "SELECT * FROM rules WHERE id = ?", (cur.lastrowid,)))


@router.patch("/{rule_id}")
def patch(rule_id: int, req: RuleReq, request: Request):
    _validate(req)
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM rules WHERE id = ?", (rule_id,))
        if not before:
            raise HTTPException(404, "no such rule")
        conn.execute("UPDATE rules SET priority = ?, name = ?, active = ?, stop = ?, match_json = ?, action_json = ? WHERE id = ?",
                     (req.priority or 100, req.name.strip(), 1 if req.active is None else req.active,
                      1 if req.stop is None else req.stop, json.dumps(req.match), json.dumps(req.action), rule_id))
        audit.log(conn, actor(request), "rule", rule_id, "patch", before, req.model_dump())
        return _out(db.row(conn, "SELECT * FROM rules WHERE id = ?", (rule_id,)))


@router.delete("/{rule_id}")
def delete(rule_id: int, request: Request):
    with db.write() as conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        audit.log(conn, actor(request), "rule", rule_id, "delete")
    return {"ok": True}


class PreviewReq(BaseModel):
    match: dict
    limit: int | None = 100


@router.post("/preview")
def preview(req: PreviewReq):
    from api.transactions import hydrate
    with db.read() as conn:
        rows = R.preview(conn, req.match, req.limit or 100)
        return {"count": len(rows), "rows": hydrate(conn, rows)}


@router.post("/{rule_id}/apply")
def apply_rule(rule_id: int, request: Request, scope: str | None = "new", force: bool | None = False):
    """scope: new (status=new) | unreviewed (same) | all. force overwrites user splits - owner's explicit call."""
    with db.write() as conn:
        if scope == "all":
            ids = [r["id"] for r in db.rows(conn, "SELECT id FROM transactions")]
        else:
            ids = [r["id"] for r in db.rows(conn, "SELECT id FROM transactions WHERE status = 'new'")]
        n = R.apply_rules(conn, ids, rule_ids=[rule_id], force=bool(force))
        audit.log(conn, actor(request), "rule", rule_id, "apply", None, {"scope": scope, "changed": n})
    return {"ok": True, "changed": n}


@router.post("/apply-all")
def apply_all(request: Request, scope: str | None = "new"):
    with db.write() as conn:
        if scope == "all":
            ids = [r["id"] for r in db.rows(conn, "SELECT id FROM transactions")]
        else:
            ids = [r["id"] for r in db.rows(conn, "SELECT id FROM transactions WHERE status = 'new'")]
        n = R.apply_rules(conn, ids)
    return {"ok": True, "changed": n}


@router.post("/from-transaction/{txn_id}")
def from_transaction(txn_id: int):
    with db.read() as conn:
        s = R.suggest_from_transaction(conn, txn_id)
        if not s:
            raise HTTPException(404, "no such transaction")
        return s
