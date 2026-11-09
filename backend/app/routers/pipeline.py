"""报价 → 订单 → 出库 → 开票 → 收款 全链路状态看板。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_any_perm
from ..models import User
from ..services import pipeline

router = APIRouter(prefix="/api/pipeline", tags=["链路看板"])


@router.get("/sales-board", dependencies=[Depends(require_any_perm("sales:view", "report:view"))])
def sales_board(db: Session = Depends(get_db)):
    return pipeline.sales_pipeline(db)