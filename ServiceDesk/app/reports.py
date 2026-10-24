"""Reports for IT staff: volumes, resolution times, problem items, warranty and stale devices."""

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import database
from .models import Category, CIStatus, ConfigItem, Priority, Status, Ticket, User, utcnow
from .security import require_staff
from .web import render

router = APIRouter()

PERIODS = {7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days", 365: "Last 12 months"}
STALE_DAYS = 90
WARRANTY_HORIZON_DAYS = 90


def _hours(ticket: Ticket) -> Optional[float]:
    if ticket.resolved_at is None:
        return None
    return (ticket.resolved_at - ticket.created_at).total_seconds() / 3600


def _avg(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return mean(values) if values else None


def build_report(db: Session, days: int) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    tickets = db.scalars(select(Ticket).where(Ticket.created_at >= since)).all()
    resolved = [t for t in tickets if t.resolved_at is not None]

    by_category = []
    for cat in Category:
        rows = [t for t in tickets if t.category == cat]
        if rows:
            by_category.append({
                "label": cat.label.split(" (")[0], "value": cat.value, "count": len(rows),
                "open": sum(t.status.is_open for t in rows), "avg_hours": _avg(_hours(t) for t in rows),
            })
    by_category.sort(key=lambda r: -r["count"])

    by_priority = []
    for prio in reversed(list(Priority)):
        rows = [t for t in tickets if t.priority == prio]
        if rows:
            by_priority.append({"label": prio.label, "count": len(rows), "avg_hours": _avg(_hours(t) for t in rows)})

    # Items with the most tickets - candidates for replacement or problem investigation.
    per_ci: dict[int, list[Ticket]] = defaultdict(list)
    for t in tickets:
        if t.ci_id:
            per_ci[t.ci_id].append(t)
    top_items = sorted(
        ({"ci": rows[0].ci, "count": len(rows),
          "incidents": sum(t.category == Category.INCIDENT for t in rows),
          "open": sum(t.status.is_open for t in rows)} for rows in per_ci.values()),
        key=lambda r: (-r["count"], -r["incidents"], r["ci"].name),
    )[:15]

    per_group: dict[str, list[Ticket]] = defaultdict(list)
    for t in tickets:
        group = t.ci.support_group.name if t.ci and t.ci.support_group else "(no item / group)"
        per_group[group].append(t)
    by_group = sorted(
        ({"name": name, "count": len(rows), "open": sum(t.status.is_open for t in rows),
          "avg_hours": _avg(_hours(t) for t in rows)} for name, rows in per_group.items()),
        key=lambda r: -r["count"],
    )

    per_agent: dict[int, list[Ticket]] = defaultdict(list)
    for t in resolved:
        if t.assignee_id:
            per_agent[t.assignee_id].append(t)
    open_by_agent: dict[int, int] = defaultdict(int)
    for t in db.scalars(select(Ticket).where(Ticket.assignee_id.is_not(None),
                                             Ticket.status.in_([s for s in Status if s.is_open]))):
        open_by_agent[t.assignee_id] += 1
    agents = {u.id: u for u in db.scalars(select(User).where(User.id.in_(set(per_agent) | set(open_by_agent))))}
    by_agent = sorted(
        ({"user": agents[uid], "resolved": len(per_agent.get(uid, [])), "open": open_by_agent.get(uid, 0),
          "avg_hours": _avg(_hours(t) for t in per_agent.get(uid, []))} for uid in agents),
        key=lambda r: (-r["resolved"], r["user"].full_name),
    )

    today = date.today()
    active = ConfigItem.status != CIStatus.RETIRED
    warranty = db.scalars(
        select(ConfigItem).where(active, ConfigItem.warranty_expires.is_not(None),
                                 ConfigItem.warranty_expires <= today + timedelta(days=WARRANTY_HORIZON_DAYS))
        .order_by(ConfigItem.warranty_expires)
    ).all()
    stale = db.scalars(
        select(ConfigItem).where(active, ConfigItem.entra_last_sign_in.is_not(None),
                                 ConfigItem.entra_last_sign_in < now - timedelta(days=STALE_DAYS))
        .order_by(ConfigItem.entra_last_sign_in)
    ).all()

    return {
        "days": days,
        "totals": {
            "created": len(tickets),
            "resolved": len(resolved),
            "open": sum(t.status.is_open for t in tickets),
            "incidents": sum(t.category == Category.INCIDENT for t in tickets),
            "avg_hours": _avg(_hours(t) for t in resolved),
            "with_item": sum(1 for t in tickets if t.ci_id),
        },
        "by_category": by_category,
        "by_priority": by_priority,
        "top_items": top_items,
        "by_group": by_group,
        "by_agent": by_agent,
        "warranty": warranty,
        "stale": stale,
        "today": today,
    }


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, days: int = 30, user: User = Depends(require_staff),
                 db: Session = Depends(database.get_db)):
    if days not in PERIODS:
        days = 30
    return render(request, "reports.html", {"r": build_report(db, days), "periods": PERIODS,
                                            "stale_days": STALE_DAYS, "warranty_days": WARRANTY_HORIZON_DAYS},
                  user=user)
