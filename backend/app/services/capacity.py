"""缸容 → 布重上限的约定换算与共用校验。

换算约定：布重千克上限 = 缸容升数 × 0.08，结果四舍五入到两位小数
（ROUND_HALF_UP，例如 800L × 0.08 = 64.00kg）。

单次新建/更新与同缸累计占用的判定共用本模块函数，避免两处口径漂移。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dye_lot import DyeLot

if TYPE_CHECKING:
    from app.models.vat import Vat

# 缸容升数 → 布重千克上限的换算系数
FABRIC_CAPACITY_FACTOR = Decimal("0.08")


def fabric_capacity_kg(capacity_l: float) -> float:
    """缸容升数换算出的布重上限（千克，四舍五入到两位）。"""
    return float(
        (Decimal(str(capacity_l)) * FABRIC_CAPACITY_FACTOR).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def vat_used_fabric_kg(db: Session, vat_id: int, exclude_lot_id: Optional[int] = None) -> float:
    """同缸所有未删除染程的布重之和（删除即物理删除，此处的行即未删除）。"""
    q = db.query(func.coalesce(func.sum(DyeLot.fabric_kg), 0.0)).filter(DyeLot.vat_id == vat_id)
    if exclude_lot_id is not None:
        q = q.filter(DyeLot.id != exclude_lot_id)
    return float(q.scalar() or 0.0)


def enforce_fabric_capacity(
    db: Session,
    vat: Vat,
    fabric_kg: float,
    exclude_lot_id: Optional[int] = None,
) -> None:
    """单次与累计共用的缸容上限校验，超限抛 400 并回显上限值。

    - 单次：本行布重不得超过上限；
    - 累计：同缸其余未删除染程布重之和 + 本行布重不得超过上限。
    两类超限都返回 400，中文提示中带上换算后的上限值。
    """
    limit = fabric_capacity_kg(vat.capacity_l)
    used = vat_used_fabric_kg(db, vat.id, exclude_lot_id=exclude_lot_id)

    limit_d = Decimal(str(limit))
    kg_d = Decimal(str(fabric_kg))
    single_over = kg_d > limit_d
    cumulative_over = Decimal(str(used)) + kg_d > limit_d

    if single_over:
        raise HTTPException(
            status_code=400,
            detail=f"布料 {round2(fabric_kg)}kg 超过该染缸布重上限 {limit:.2f}kg"
            f"（缸容 {vat.capacity_l:g}L × 0.08）",
        )
    if cumulative_over:
        raise HTTPException(
            status_code=400,
            detail=f"同缸累计布重 {round2(used + fabric_kg):.2f}kg 超过该染缸布重上限"
            f" {limit:.2f}kg（缸容 {vat.capacity_l:g}L × 0.08）",
        )


def round2(value: float) -> float:
    """四舍五入到两位（ROUND_HALF_UP），用于提示中的数值回显。"""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
