from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

import attachments as A
import audit
import config
import db
import reports
from api import actor

router = APIRouter(prefix="/api/attachments")


@router.post("")
async def upload(request: Request, file: UploadFile = File(...), kind: str = Form("receipt"),
                 transaction_id: int | None = Form(None), payee_id: int | None = Form(None)):
    if kind not in ("receipt", "invoice", "w9", "statement", "other"):
        raise HTTPException(400, "bad kind")
    data = await file.read()
    with db.write() as conn:
        try:
            aid, created = A.store(conn, data, file.filename or "", kind, transaction_id, payee_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit.log(conn, actor(request), "attachment", aid, "upload", None,
                  {"transaction_id": transaction_id, "payee_id": payee_id, "name": file.filename, "bytes": len(data), "new": created})
        return db.row(conn, "SELECT id, kind, original_name, bytes, added_at, transaction_id, payee_id FROM attachments WHERE id = ?", (aid,))


@router.get("/missing")
def missing(year: int | None = None):
    year = year or config.today().year
    with db.read() as conn:
        rows = reports.missing_receipts(conn, year, config.CFG.get("receipt_required_over_cents", 7500))
    return {"year": year, "threshold_cents": config.CFG.get("receipt_required_over_cents", 7500), "rows": rows}


@router.get("/{attachment_id}")
def get_file(attachment_id: int, download: int | None = 0):
    with db.read() as conn:
        a = db.row(conn, "SELECT * FROM attachments WHERE id = ?", (attachment_id,))
    if not a:
        raise HTTPException(404, "no such attachment")
    path = A.full_path(a["path"])
    disposition = "attachment" if download else "inline"
    return FileResponse(path, filename=a["original_name"], content_disposition_type=disposition)


@router.delete("/{attachment_id}")
def delete(attachment_id: int, request: Request):
    with db.write() as conn:
        if not A.delete(conn, attachment_id):
            raise HTTPException(404, "no such attachment")
        audit.log(conn, actor(request), "attachment", attachment_id, "delete")
    return {"ok": True}
