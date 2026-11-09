"""盘点差异 → 库存调整单。

盘点单在审批通过时已经改过库存，所以这里生成的调整单是**追溯凭证**：
直接以「已过账」状态落库，不再重复动库存，避免盘盈盘亏被算两遍。
单据上用 source_type/source_no 指向盘点单，既方便追溯，也保证不会重复生成。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import StockAdjustment, StockAdjustmentItem, StocktakeOrder
from ..utils import gen_order_no

SOURCE_TYPE = "stocktake"
TYPE_TEXT = {"gain": "盘盈入库", "loss": "盘亏出库"}


def existing(db: Session, stocktake_no: str) -> list[StockAdjustment]:
    return list(
        db.scalars(
            select(StockAdjustment)
            .where(
                StockAdjustment.source_type == SOURCE_TYPE,
                StockAdjustment.source_no == stocktake_no,
            )
            .order_by(StockAdjustment.id)
        ).all()
    )


def generate(db: Session, stocktake: StocktakeOrder, username: str) -> list[StockAdjustment]:
    """按盘点差异生成盘盈/盘亏调整单（已过账、不再动库存）；重复调用返回已有单据。"""
    if stocktake.status != "completed":
        raise ValueError("只有已完成的盘点单可以生成调整单")
    exists = existing(db, stocktake.stocktake_no)
    if exists:
        return exists

    diffs = [item for item in stocktake.items if item.diff_qty]
    if not diffs:
        return []

    gains = [item for item in diffs if item.diff_qty > 0]
    losses = [item for item in diffs if item.diff_qty < 0]
    reason = f"由盘点单 {stocktake.stocktake_no} 生成；库存已在盘点审批时调整，本单为追溯凭证"
    created: list[StockAdjustment] = []
    for adjust_type, rows in (("gain", gains), ("loss", losses)):
        if not rows:
            continue
        doc = StockAdjustment(
            adjust_no=gen_order_no(db, StockAdjustment, "ADJ", field="adjust_no"),
            adjust_type=adjust_type,
            warehouse_id=stocktake.warehouse_id,
            status="posted",
            posted_at=datetime.now(),
            reason=reason,
            source_type=SOURCE_TYPE,
            source_no=stocktake.stocktake_no,
            created_by=username,
        )
        total = 0.0
        for item in rows:
            product = item.product
            quantity = abs(int(item.diff_qty))
            unit_cost = float(product.cost_price or 0) if product else 0.0
            amount = round(quantity * unit_cost, 2)
            total += amount
            doc.items.append(
                StockAdjustmentItem(
                    product_id=item.product_id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    amount=amount,
                )
            )
        doc.total_amount = round(total, 2)
        db.add(doc)
        db.flush()  # 让下一张单取号时能看到已生成的单号，避免撞号
        created.append(doc)
    db.commit()
    for doc in created:
        db.refresh(doc)
    return created


def serialize(doc: StockAdjustment) -> dict:
    return {
        "id": doc.id,
        "adjust_no": doc.adjust_no,
        "adjust_type": doc.adjust_type,
        "adjust_type_name": TYPE_TEXT.get(doc.adjust_type, doc.adjust_type),
        "status": doc.status,
        "total_amount": doc.total_amount,
        "source_type": doc.source_type,
        "source_no": doc.source_no,
    }
