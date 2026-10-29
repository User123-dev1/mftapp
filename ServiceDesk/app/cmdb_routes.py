"""CMDB pages: configuration items, CSV import/export and support groups."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import changes, cmdb, database, entra_sync, sso
from .config import settings
from .models import ChangeRequest, CIStatus, CIType, ConfigItem, Status, SupportGroup, Ticket, User
from .security import Forbidden, flash, require_manager, require_staff, verify_csrf
from .web import redirect, render

router = APIRouter()

_TEXT_FIELDS = ("asset_tag", "serial_number", "manufacturer", "model", "version", "ip_address", "location",
                "vendor", "notes")


def _staff_and_groups(db: Session):
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)).all()
    groups = db.scalars(select(SupportGroup).order_by(SupportGroup.name)).all()
    return users, groups


def _apply_form(db: Session, ci: ConfigItem, form) -> Optional[str]:
    """Copy submitted fields onto ci. Returns an error message, or None if valid."""
    name = str(form.get("name", "")).strip()
    if not name:
        return "Name is required."
    try:
        ci_type = CIType(form.get("ci_type", ""))
        status = CIStatus(form.get("status", "in_use"))
    except ValueError:
        return "Choose a valid type and status."

    warranty = None
    if form.get("warranty_expires"):
        try:
            warranty = date.fromisoformat(str(form["warranty_expires"]))
        except ValueError:
            return "Warranty date must be YYYY-MM-DD."

    values = {f: (str(form.get(f, "")).strip() or None) for f in _TEXT_FIELDS}
    if values["asset_tag"]:
        stmt = select(ConfigItem).where(ConfigItem.asset_tag == values["asset_tag"])
        if ci.id is not None:
            stmt = stmt.where(ConfigItem.id != ci.id)
        clash = db.scalar(stmt)
        if clash:
            return f"Asset tag {values['asset_tag']} is already used by {clash.name}."

    owner = None
    owner_email = str(form.get("assigned_to_email", "")).strip().lower()
    if owner_email:
        owner = db.scalar(select(User).where(User.email == owner_email))
        if owner is None:
            return f"No user with email {owner_email}."

    group = None
    if str(form.get("support_group_id", "")).isdigit():
        group = db.get(SupportGroup, int(form["support_group_id"]))

    ci.name, ci.ci_type, ci.status, ci.warranty_expires = name, ci_type, status, warranty
    for f, v in values.items():
        setattr(ci, f, v)
    ci.assigned_user, ci.support_group = owner, group
    ci.change_controlled = form.get("change_controlled") == "true"
    return None


# --- Items --------------------------------------------------------------------

@router.get("/cmdb", response_class=HTMLResponse)
def list_items(
    request: Request,
    type: str = "",
    status: str = "active",
    group: str = "",
    q: str = "",
    user: User = Depends(require_staff),
    db: Session = Depends(database.get_db),
):
    stmt = select(ConfigItem).outerjoin(User, ConfigItem.assigned_user_id == User.id)
    if type:
        try:
            stmt = stmt.where(ConfigItem.ci_type == CIType(type))
        except ValueError:
            pass
    if status == "active":
        stmt = stmt.where(ConfigItem.status != CIStatus.RETIRED)
    elif status and status != "all":
        try:
            stmt = stmt.where(ConfigItem.status == CIStatus(status))
        except ValueError:
            pass
    if group == "none":
        stmt = stmt.where(ConfigItem.support_group_id.is_(None))
    elif group.isdigit():
        stmt = stmt.where(ConfigItem.support_group_id == int(group))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            ConfigItem.name.ilike(like), ConfigItem.asset_tag.ilike(like), ConfigItem.serial_number.ilike(like),
            ConfigItem.model.ilike(like), ConfigItem.ip_address.ilike(like), User.email.ilike(like),
            User.full_name.ilike(like),
        ))
    items = db.scalars(stmt.order_by(ConfigItem.name).limit(1000)).all()

    open_statuses = [s for s in Status if s.is_open]
    totals = dict(db.execute(select(Ticket.ci_id, func.count()).where(Ticket.ci_id.is_not(None)).group_by(Ticket.ci_id)).all())
    open_counts = dict(db.execute(
        select(Ticket.ci_id, func.count())
        .where(Ticket.ci_id.is_not(None), Ticket.status.in_(open_statuses))
        .group_by(Ticket.ci_id)
    ).all())
    groups = db.scalars(select(SupportGroup).order_by(SupportGroup.name)).all()
    return render(request, "cmdb_list.html", {
        "items": items, "totals": totals, "open_counts": open_counts, "groups": groups,
        "filters": {"type": type, "status": status, "group": group, "q": q},
    }, user=user)


@router.get("/cmdb/new", response_class=HTMLResponse)
def new_item_page(request: Request, user: User = Depends(require_staff), db: Session = Depends(database.get_db)):
    users, groups = _staff_and_groups(db)
    return render(request, "cmdb_item.html", {"ci": None, "form": {}, "users": users, "groups": groups,
                                              "tickets": []}, user=user)


@router.post("/cmdb/new")
async def create_item(request: Request, user: User = Depends(require_staff), db: Session = Depends(database.get_db),
                      _: None = Depends(verify_csrf)):
    form = await request.form()
    ci = ConfigItem()
    error = _apply_form(db, ci, form)
    if error:
        users, groups = _staff_and_groups(db)
        flash(request, error, "error")
        return render(request, "cmdb_item.html", {"ci": None, "form": dict(form), "users": users, "groups": groups,
                                                  "tickets": []}, status_code=400, user=user)
    db.add(ci)
    db.commit()
    flash(request, f"Added {ci.name}.")
    return redirect(f"/cmdb/{ci.id}")


@router.get("/cmdb/{ci_id:int}", response_class=HTMLResponse)
def item_detail(request: Request, ci_id: int, user: User = Depends(require_staff),
                db: Session = Depends(database.get_db)):
    ci = db.get(ConfigItem, ci_id)
    if ci is None:
        raise HTTPException(status_code=404, detail="Item not found")
    users, groups = _staff_and_groups(db)
    from .services import scoped
    tickets = db.scalars(scoped(select(Ticket), user).where(Ticket.ci_id == ci.id)
                         .order_by(Ticket.created_at.desc())).all()
    others = db.scalars(select(ConfigItem).where(ConfigItem.id != ci.id, ConfigItem.status != CIStatus.RETIRED)
                        .order_by(ConfigItem.name)).all()
    ci_changes = db.scalars(select(ChangeRequest).where(ChangeRequest.items.any(ConfigItem.id == ci.id))
                            .order_by(ChangeRequest.id.desc())).all()
    return render(request, "cmdb_item.html", {
        "ci": ci, "form": {}, "users": users, "groups": groups, "tickets": tickets,
        "depends_on": changes.depends_on(db, ci), "used_by": changes.used_by(db, ci),
        "other_items": others, "changes_for_ci": ci_changes,
    }, user=user)


@router.post("/cmdb/{ci_id:int}")
async def update_item(request: Request, ci_id: int, user: User = Depends(require_staff),
                      db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    ci = db.get(ConfigItem, ci_id)
    if ci is None:
        raise HTTPException(status_code=404, detail="Item not found")
    error = _apply_form(db, ci, await request.form())
    if error:
        db.rollback()
        flash(request, error, "error")
    else:
        db.commit()
        flash(request, f"Saved {ci.name}.")
    return redirect(f"/cmdb/{ci.id}")


# --- Import / export -----------------------------------------------------------

@router.get("/cmdb/import", response_class=HTMLResponse)
def import_page(request: Request, user: User = Depends(require_manager)):
    return render(request, "cmdb_import.html", {"result": None, "columns": cmdb.CSV_COLUMNS}, user=user)


@router.post("/cmdb/import", response_class=HTMLResponse)
async def import_items(request: Request, file: UploadFile = File(...), user: User = Depends(require_manager),
                       db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    data = await file.read(cmdb.MAX_IMPORT_BYTES + 1)
    if len(data) > cmdb.MAX_IMPORT_BYTES:
        flash(request, "File is larger than 5 MB.", "error")
        return redirect("/cmdb/import")
    result = cmdb.import_csv(db, data, allow_sso_placeholders=bool(sso.providers()))
    db.commit()
    return render(request, "cmdb_import.html", {"result": result, "columns": cmdb.CSV_COLUMNS}, user=user)


def _csv_response(body: str, filename: str) -> Response:
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/cmdb/template.csv")
def import_template(user: User = Depends(require_staff)):
    return _csv_response(cmdb.template_csv(), "cmdb_template.csv")


@router.get("/cmdb/export.csv")
def export_items(user: User = Depends(require_staff), db: Session = Depends(database.get_db)):
    return _csv_response(cmdb.export_csv(db), f"cmdb_export_{date.today().isoformat()}.csv")


# --- Support groups --------------------------------------------------------------

@router.get("/admin/groups", response_class=HTMLResponse)
def groups_page(request: Request, user: User = Depends(require_manager), db: Session = Depends(database.get_db)):
    groups = db.scalars(select(SupportGroup).order_by(SupportGroup.name)).all()
    staff = [u for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)) if u.is_staff]
    item_counts = dict(db.execute(
        select(ConfigItem.support_group_id, func.count()).group_by(ConfigItem.support_group_id)
    ).all())
    return render(request, "groups.html", {"groups": groups, "staff": staff, "item_counts": item_counts}, user=user)


@router.post("/admin/groups")
def create_group(request: Request, name: str = Form(...), description: str = Form(""),
                 user: User = Depends(require_manager), db: Session = Depends(database.get_db),
                 _: None = Depends(verify_csrf)):
    name = name.strip()
    if not name:
        flash(request, "Group name is required.", "error")
    elif db.scalar(select(SupportGroup).where(func.lower(SupportGroup.name) == name.lower())):
        flash(request, "A group with that name already exists.", "error")
    else:
        db.add(SupportGroup(name=name, description=description.strip() or None))
        db.commit()
        flash(request, f"Created group {name}.")
    return redirect("/admin/groups")


@router.post("/admin/groups/{group_id:int}")
async def update_group(request: Request, group_id: int, user: User = Depends(require_manager),
                       db: Session = Depends(database.get_db), _: None = Depends(verify_csrf)):
    group = db.get(SupportGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        flash(request, "Group name is required.", "error")
        return redirect("/admin/groups")
    clash = db.scalar(select(SupportGroup).where(func.lower(SupportGroup.name) == name.lower(),
                                                 SupportGroup.id != group.id))
    if clash:
        flash(request, "A group with that name already exists.", "error")
        return redirect("/admin/groups")
    member_ids = {int(v) for v in form.getlist("member_ids") if str(v).isdigit()}
    members = db.scalars(select(User).where(User.id.in_(member_ids))).all() if member_ids else []
    if any(not m.is_staff for m in members):
        raise Forbidden()
    group.name = name
    group.description = str(form.get("description", "")).strip() or None
    group.members = list(members)
    group.auto_assign = form.get("auto_assign") == "true"
    if group.auto_assign and not members:
        flash(request, f"{group.name} has no members, so nothing will be auto-assigned yet.", "info")
    db.commit()
    flash(request, f"Saved {group.name}.")
    return redirect("/admin/groups")


# --- Entra device sync ------------------------------------------------------------

@router.get("/cmdb/sync", response_class=HTMLResponse)
def sync_page(request: Request, user: User = Depends(require_manager), db: Session = Depends(database.get_db)):
    runs = entra_sync.last_runs(db)
    synced = db.scalar(select(func.count()).select_from(ConfigItem).where(ConfigItem.entra_object_id.is_not(None)))
    return render(request, "cmdb_sync.html", {
        "runs": runs,
        "running": any(r.status == "running" for r in runs),
        "configured": entra_sync.is_configured(),
        "synced_count": synced,
        "settings": settings,
    }, user=user)


@router.post("/cmdb/sync")
def sync_now(request: Request, background: BackgroundTasks, user: User = Depends(require_manager),
             _: None = Depends(verify_csrf)):
    if not entra_sync.is_configured():
        flash(request, "Entra sync is not configured. See the setup steps below.", "error")
    else:
        background.add_task(entra_sync.run_sync, "manual")
        flash(request, "Sync started. This page refreshes until it finishes.")
    return redirect("/cmdb/sync")
