from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.dye_lot import DyeLot
from app.models.user import User
from app.models.vat import Vat
from app.schemas.dye_lot import DyeLotCreate, DyeLotUpdate, DyeLotOut
from app.services.capacity import (
    at_capacity_lot_ids,
    ensure_fabric_within_capacity,
)

router = APIRouter(prefix="/api/dye-lots", tags=["dye-lots"])

ALLOWED_VAT_STATUSES = {"ready", "dyeing"}


def _serialize(db: Session, lot: DyeLot) -> DyeLotOut:
    # 触顶按整缸全部染程累计判定，保证单条查看与列表口径一致
    out = DyeLotOut.model_validate(lot)
    out.at_capacity = lot.id in at_capacity_lot_ids(db)
    return out


@router.get("", response_model=List[DyeLotOut])
def list_dye_lots(
    vat_id: Optional[int] = Query(None, alias="vatId"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(DyeLot)
    if vat_id is not None:
        q = q.filter(DyeLot.vat_id == vat_id)
    items = q.order_by(DyeLot.id.desc()).all()
    at_ids = at_capacity_lot_ids(db)
    result = []
    for item in items:
        out = DyeLotOut.model_validate(item)
        out.at_capacity = item.id in at_ids
        result.append(out)
    return result


@router.post("", response_model=DyeLotOut, status_code=status.HTTP_201_CREATED)
def create_dye_lot(
    payload: DyeLotCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    vat = db.query(Vat).filter(Vat.id == payload.vat_id).first()
    if not vat:
        raise HTTPException(status_code=400, detail="染缸不存在")
    if vat.status not in ALLOWED_VAT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"染缸状态为「{vat.status}」，仅 ready 或 dyeing 时可新建染程",
        )
    ensure_fabric_within_capacity(
        db,
        vat_id=payload.vat_id,
        fabric_kg=payload.fabric_kg,
        capacity_l=vat.capacity_l,
    )
    item = DyeLot(
        vat_id=payload.vat_id,
        recipe_name=payload.recipe_name,
        fabric_kg=payload.fabric_kg,
        started_at=payload.started_at,
        operator_name=payload.operator_name,
    )
    vat.status = "dyeing"
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize(db, item)


@router.get("/{lot_id}", response_model=DyeLotOut)
def get_dye_lot(
    lot_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = db.query(DyeLot).filter(DyeLot.id == lot_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="染程不存在")
    return _serialize(db, item)


@router.put("/{lot_id}", response_model=DyeLotOut)
def update_dye_lot(
    lot_id: int,
    payload: DyeLotUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = db.query(DyeLot).filter(DyeLot.id == lot_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="染程不存在")
    data = payload.model_dump(exclude_unset=True)

    target_vat_id = data.get("vat_id", item.vat_id)
    target_vat = item.vat
    vat_changed = "vat_id" in data and data["vat_id"] != item.vat_id
    if vat_changed:
        target_vat = db.query(Vat).filter(Vat.id == data["vat_id"]).first()
        if not target_vat:
            raise HTTPException(status_code=400, detail="染缸不存在")
        if target_vat.status not in ALLOWED_VAT_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"目标染缸状态为「{target_vat.status}」，无法改挂染程",
            )

    if "fabric_kg" in data or vat_changed:
        # 布重或染缸变更都要校验：单次 + 累计共用函数；
        # 留在原缸时累计排除自身，改挂新缸时按新缸全量累计
        effective_kg = data.get("fabric_kg", item.fabric_kg)
        ensure_fabric_within_capacity(
            db,
            vat_id=target_vat_id,
            fabric_kg=effective_kg,
            capacity_l=target_vat.capacity_l,
            exclude_lot_id=item.id if not vat_changed else None,
        )

    if vat_changed:
        target_vat.status = "dyeing"
    for k, v in data.items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return _serialize(db, item)


@router.delete("/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dye_lot(
    lot_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = db.query(DyeLot).filter(DyeLot.id == lot_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="染程不存在")
    db.delete(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="该染程仍有关联记录，无法删除")
