"""风险雷达接口：一次请求拿到当前账号可见的全部风险条目。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_perm
from ..models import User
from ..services import risk

router = APIRouter(prefix="/api/risk-radar", tags=["风险雷达"])


@router.get("", summary="风险雷达（按角色过滤）")
def radar(
    db: Session = Depends(get_db),
    user: User = Depends(require_perm("dashboard:view")),
):
    return risk.build(db, user)
