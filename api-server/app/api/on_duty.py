# -*- coding: utf-8 -*-
"""值班排班 + 节假日 + 换班/代班 API"""
from datetime import date as _date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import DutyOverride, Holiday, OnDutySchedule, User

router = APIRouter(prefix="/api/v1")


def _duty_dict(s: OnDutySchedule) -> dict:
    return {
        "id": s.id, "name": s.name, "day_type": s.day_type, "shift_type": s.shift_type,
        "start_time": s.start_time, "end_time": s.end_time, "user_ids": s.user_ids, "enabled": s.enabled,
    }


# ---------- 值班排班 ----------
@router.get("/on-duty")
async def list_duty(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(OnDutySchedule).order_by(OnDutySchedule.id))
        return {"code": 200, "message": "success", "data": [_duty_dict(s) for s in result.scalars()]}


@router.post("/on-duty")
async def create_duty(payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        s = OnDutySchedule(
            name=payload.get("name", ""),
            day_type=payload.get("day_type", "weekday"),
            shift_type=payload.get("shift_type", "day"),
            start_time=payload.get("start_time", "09:00"),
            end_time=payload.get("end_time", "18:00"),
            user_ids=payload.get("user_ids", ""),
            enabled=payload.get("enabled", True),
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
    return {"code": 200, "message": "success", "data": _duty_dict(s)}


@router.put("/on-duty/{duty_id}")
async def update_duty(duty_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        s = await session.get(OnDutySchedule, duty_id)
        if not s:
            return {"code": 404, "message": "排班不存在", "data": None}
        for f in ("name", "day_type", "shift_type", "start_time", "end_time", "user_ids", "enabled"):
            if f in payload:
                setattr(s, f, payload[f])
        await session.commit()
        await session.refresh(s)
    return {"code": 200, "message": "success", "data": _duty_dict(s)}


@router.delete("/on-duty/{duty_id}")
async def delete_duty(duty_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        s = await session.get(OnDutySchedule, duty_id)
        if not s:
            return {"code": 404, "message": "排班不存在", "data": None}
        await session.delete(s)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


# ---------- 换班 / 代班（C6） ----------
@router.get("/on-duty/overrides")
async def list_overrides(start: str = "", end: str = "", user=Depends(require_any_user)):
    async with async_session() as session:
        q = select(DutyOverride)
        if start:
            q = q.where(DutyOverride.date >= start)
        if end:
            q = q.where(DutyOverride.date <= end)
        rows = list((await session.execute(q.order_by(DutyOverride.date))).scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": r.id, "date": r.date, "original_username": r.original_username,
         "substitute_username": r.substitute_username, "type": r.type, "note": r.note,
         "created_by": r.created_by, "created_at": str(r.created_at)} for r in rows
    ]}


@router.post("/on-duty/overrides")
async def create_override(payload: dict, user=Depends(require_admin)):
    """新建换班/代班：date + original_username(可空=整班代) + substitute_username + type + note"""
    d = (payload.get("date") or "").strip()
    sub = (payload.get("substitute_username") or "").strip()
    if not d or not sub:
        return {"code": 400, "message": "date 与 substitute_username 必填", "data": None}
    async with async_session() as session:
        target = (await session.execute(select(User).where(User.username == sub))).scalars().first()
        if not target:
            return {"code": 400, "message": f"用户不存在：{sub}", "data": None}
        o = DutyOverride(
            date=d, original_username=(payload.get("original_username") or "").strip(),
            substitute_username=sub, type=(payload.get("type") or "swap"),
            note=(payload.get("note") or "")[:255], created_by=getattr(user, "username", ""),
        )
        session.add(o)
        await session.commit()
        await session.refresh(o)
    return {"code": 200, "message": "success", "data": {"id": o.id}}


@router.delete("/on-duty/overrides/{override_id}")
async def delete_override(override_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        o = await session.get(DutyOverride, override_id)
        if not o:
            return {"code": 404, "message": "记录不存在", "data": None}
        await session.delete(o)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.get("/on-duty/calendar")
async def duty_calendar(start: str = "", end: str = "", user=Depends(require_any_user)):
    """值班日历：按天给出当日值班人（排班规则 + 节假日 + 换班/代班覆盖）"""
    today = _date.today()
    try:
        s = _date.fromisoformat(start) if start else today.replace(day=1)
        e = _date.fromisoformat(end) if end else (s + timedelta(days=41))
    except ValueError:
        return {"code": 400, "message": "日期格式应为 YYYY-MM-DD", "data": None}

    async with async_session() as session:
        schedules = list((await session.execute(
            select(OnDutySchedule).where(OnDutySchedule.enabled.is_(True))
        )).scalars())
        holidays = {h.date: h.name for h in (await session.execute(select(Holiday))).scalars()}
        overrides = list((await session.execute(
            select(DutyOverride).where(DutyOverride.date >= s.isoformat(), DutyOverride.date <= e.isoformat())
        )).scalars())
        users = {u.id: u.username for u in (await session.execute(select(User))).scalars()}

    day = s
    days = []
    while day <= e:
        ds = day.isoformat()
        if day in (s, ) or True:
            pass
        day_type = "holiday" if ds in holidays else ("weekend" if day.weekday() >= 5 else "weekday")
        names: list[str] = []
        for sc in schedules:
            if sc.day_type != day_type:
                continue
            for uid in (sc.user_ids or "").split(","):
                uid = uid.strip()
                if uid:
                    nm = users.get(int(uid), uid) if uid.isdigit() else uid
                    if nm not in names:
                        names.append(nm)
        # 应用换班/代班
        day_ovr = [o for o in overrides if o.date == ds]
        for o in day_ovr:
            if o.original_username:
                names = [o.substitute_username if n == o.original_username else n for n in names]
            else:
                if o.substitute_username not in names:
                    names.append(o.substitute_username)
        days.append({
            "date": ds, "day_type": day_type, "holiday": holidays.get(ds, ""),
            "users": names,
            "overrides": [{"id": o.id, "original": o.original_username, "substitute": o.substitute_username,
                           "type": o.type, "note": o.note} for o in day_ovr],
        })
        day += timedelta(days=1)
    return {"code": 200, "message": "success", "data": {"start": s.isoformat(), "end": e.isoformat(), "days": days}}


# ---------- 节假日 ----------
@router.get("/holidays")
async def list_holidays(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(Holiday).order_by(Holiday.date))
        return {"code": 200, "message": "success", "data": [
            {"id": h.id, "date": h.date, "name": h.name} for h in result.scalars()
        ]}


@router.post("/holidays")
async def create_holiday(payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        h = Holiday(date=payload.get("date", ""), name=payload.get("name", ""))
        session.add(h)
        await session.commit()
        await session.refresh(h)
    return {"code": 200, "message": "success", "data": {"id": h.id, "date": h.date, "name": h.name}}


@router.delete("/holidays/{holiday_id}")
async def delete_holiday(holiday_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        h = await session.get(Holiday, holiday_id)
        if not h:
            return {"code": 404, "message": "节假日不存在", "data": None}
        await session.delete(h)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
