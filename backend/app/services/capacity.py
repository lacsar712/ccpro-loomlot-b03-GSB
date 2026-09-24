"""缸容换算与布重上限校验。

约定换算：布重千克上限 = 染缸缸容升数 × 0.08，结果四舍五入到两位小数。
单次校验（单个染程布重）与累计校验（同缸所有染程布重之和）共用
:func:`ensure_fabric_within_capacity`；触顶标记（列表与看板）共用
:func:`at_capacity_lot_ids`，保证两边行数对得上。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Set

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dye_lot import DyeLot
from app.models.vat import Vat

# 布重 kg 上限相对缸容 L 的换算系数
FABRIC_CAPACITY_FACTOR = 0.08

_TWO_PLACES = Decimal("0.01")


def fabric_capacity_kg(capacity_l: float) -> float:
    """缸容升数 → 布重千克上限（× 0.08，四舍五入到两位）。"""
    limit = (Decimal(str(capacity_l)) * Decimal(str(FABRIC_CAPACITY_FACTOR))).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    return float(limit)


def vat_fabric_total_kg(db: Session, vat_id: int, exclude_lot_id: Optional[int] = None) -> float:
    """同缸（未删除）染程布重之和；编辑时可排除自身。"""
    q = db.query(func.coalesce(func.sum(DyeLot.fabric_kg), 0.0)).filter(DyeLot.vat_id == vat_id)
    if exclude_lot_id is not None:
        q = q.filter(DyeLot.id != exclude_lot_id)
    return float(q.scalar() or 0.0)


def _exceeds(value_kg: float, limit_kg: float) -> bool:
    # 统一到两位小数再比较，规避浮点尾数造成的临界误判
    return Decimal(str(value_kg)) > Decimal(str(limit_kg))


def at_capacity_lot_ids(db: Session) -> Set[int]:
    """全量触顶染程 ID 集合 —— 列表与看板共用同一份结果，保证两边行数一致。

    按缸分组、按建程顺序（id 升序）逐程累计布重；使某缸累计「恰好达到」
    该缸布重上限（缸容 × 0.08，两位小数）的那一程记为触顶，每缸至多一条。
    单缸布重本身恰好等于上限时，自然就是该缸第一条触顶行。

    始终在全部未删除染程上计算（累计链不可被时间/分页切断）；
    调用方各自取交集：列表按本行 ID，看板再叠加「本周 started_at」。
    """
    result: Set[int] = set()
    limit_by_vat: dict = {}
    running_by_vat: dict = {}
    lots = (
        db.query(DyeLot)
        .join(Vat, DyeLot.vat_id == Vat.id)
        .order_by(DyeLot.vat_id, DyeLot.id)
        .all()
    )
    for lot in lots:
        limit = limit_by_vat.setdefault(
            lot.vat_id, Decimal(str(fabric_capacity_kg(lot.vat.capacity_l)))
        )
        running = running_by_vat.get(lot.vat_id, Decimal("0")) + Decimal(str(lot.fabric_kg))
        running_by_vat[lot.vat_id] = running
        if running == limit:
            result.add(lot.id)
    return result


def ensure_fabric_within_capacity(
    db: Session,
    vat_id: int,
    fabric_kg: float,
    capacity_l: float,
    exclude_lot_id: Optional[int] = None,
) -> None:
    """单次与累计共用的布重上限校验，超限直接抛 400（中文回显上限值）。

    - 单次：本次染程布重不得超过该缸布重上限；
    - 累计：同缸其他（未删除）染程布重 + 本次布重不得超过同一上限。
    """
    limit = fabric_capacity_kg(capacity_l)

    if _exceeds(fabric_kg, limit):
        raise HTTPException(
            status_code=400,
            detail=f"布重 {fabric_kg} kg 超过该染缸单次上限 {limit} kg（缸容 {capacity_l} L × 0.08）",
        )

    other_total = vat_fabric_total_kg(db, vat_id, exclude_lot_id=exclude_lot_id)
    if _exceeds(other_total + fabric_kg, limit):
        raise HTTPException(
            status_code=400,
            detail=(
                f"同缸染程累计布重 {round(other_total + fabric_kg, 2)} kg "
                f"超过该染缸上限 {limit} kg（缸容 {capacity_l} L × 0.08）"
            ),
        )
