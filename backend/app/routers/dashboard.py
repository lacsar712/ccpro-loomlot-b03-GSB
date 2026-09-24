from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.dye_house import DyeHouse
from app.models.dye_lot import DyeLot
from app.models.fastness_check import FastnessCheck
from app.models.user import User
from app.models.vat import Vat
from app.schemas.dashboard import DashboardStats
from app.services.capacity import at_capacity_lot_ids

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    # 本周一 00:00（周一为一周起点）
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # 与染程列表同源的触顶集合，再叠加「本周」过滤 → 两边行数必然一致
    at_ids = at_capacity_lot_ids(db)
    at_capacity_this_week = (
        db.query(func.count(DyeLot.id))
        .filter(DyeLot.id.in_(at_ids), DyeLot.started_at >= week_start)
        .scalar()
        or 0
    )

    return DashboardStats(
        dye_house_total=db.query(func.count(DyeHouse.id)).scalar() or 0,
        vat_ready_count=db.query(func.count(Vat.id)).filter(Vat.status == "ready").scalar() or 0,
        vat_dyeing_count=db.query(func.count(Vat.id)).filter(Vat.status == "dyeing").scalar() or 0,
        lots_last_7d=(
            db.query(func.count(DyeLot.id))
            .filter(DyeLot.started_at >= now - timedelta(days=7))
            .scalar()
            or 0
        ),
        at_capacity_this_week=at_capacity_this_week,
        checks_last_24h=(
            db.query(func.count(FastnessCheck.id))
            .filter(FastnessCheck.checked_at >= now - timedelta(hours=24))
            .scalar()
            or 0
        ),
    )
