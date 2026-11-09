from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..deps import require_perm
from ..services import backup

router = APIRouter(prefix="/api/backups", tags=["数据备份"])


@router.get("", dependencies=[Depends(require_perm("user:manage"))])
def list_backups():
    return {"items": backup.list_backups()}


@router.post("", dependencies=[Depends(require_perm("user:manage"))])
def create_backup():
    return backup.create_backup()


@router.get("/{name}/download", dependencies=[Depends(require_perm("user:manage"))])
def download_backup(name: str):
    try:
        path = backup._safe_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(path, filename=name, media_type="application/zip")


@router.post("/{name}/restore", dependencies=[Depends(require_perm("user:manage"))])
def restore_backup(name: str, payload: dict | None = None):
    if not (payload or {}).get("confirm"):
        raise HTTPException(status_code=400, detail="恢复会覆盖当前全部数据，请显式确认（confirm=true）")
    try:
        return {"restored": backup.restore_backup(name)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{name}", dependencies=[Depends(require_perm("user:manage"))])
def delete_backup(name: str):
    try:
        backup.delete_backup(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}