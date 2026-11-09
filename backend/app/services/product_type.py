"""物料类型约束：统一原料/成品的业务校验与提示。"""
from fastapi import HTTPException

from ..models import Product
from .importer import PRODUCT_TYPE_LABELS


def type_label(product_type: str) -> str:
    return PRODUCT_TYPE_LABELS.get(product_type, product_type)


def ensure_product_type(product: Product, expected: str, scene: str) -> None:
    """校验商品类型，不符合时返回带商品与场景的 400。"""
    if product.product_type != expected:
        raise HTTPException(
            status_code=400,
            detail=(
                f"所选商品「{product.sku} {product.name}」是{type_label(product.product_type)}，"
                f"{scene}只能选择{type_label(expected)}"
            ),
        )
