from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import config
import db
from api import actor

router = APIRouter(prefix="/api/credit")


@router.get("")
def scores(limit: int | None = 200):
    with db.read() as conn:
        rows = db.rows(conn, "SELECT * FROM credit_scores ORDER BY date DESC, id DESC LIMIT ?", (limit or 200,))
    latest = rows[0] if rows else None
    prev = None
    if latest:
        for r in rows[1:]:
            if r["source"] == latest["source"] and r["date"] < latest["date"]:
                prev = r
                break
    return {"rows": rows, "latest": latest, "previous_same_source": prev,
            "delta": (latest["score"] - prev["score"]) if latest and prev else None}


class ScoreReq(BaseModel):
    score: int
    date: str | None = None
    source: str | None = ""
    model: str | None = ""
    notes: str | None = ""


@router.post("")
def add(req: ScoreReq, request: Request):
    if not 300 <= req.score <= 850:
        raise HTTPException(400, "a score is between 300 and 850")
    with db.write() as conn:
        cur = conn.execute("INSERT INTO credit_scores (date, score, source, model, notes) VALUES (?, ?, ?, ?, ?)",
                           (req.date or config.today().isoformat(), req.score, req.source or "", req.model or "", req.notes or ""))
        audit.log(conn, actor(request), "credit_score", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM credit_scores WHERE id = ?", (cur.lastrowid,))


@router.delete("/{sid}")
def delete(sid: int, request: Request):
    with db.write() as conn:
        conn.execute("DELETE FROM credit_scores WHERE id = ?", (sid,))
        audit.log(conn, actor(request), "credit_score", sid, "delete")
    return {"ok": True}
