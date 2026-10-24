"""CMDB rules: what a requester may pick, assignment suggestions, CSV import/export."""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import CIStatus, CIType, ConfigItem, Role, Status, SupportGroup, Ticket, User
from .security import NO_PASSWORD

CSV_COLUMNS = [
    "name", "type", "status", "asset_tag", "serial_number", "manufacturer", "model", "version",
    "ip_address", "location", "vendor", "warranty_expires", "assigned_to_email", "support_group", "notes",
]
MAX_IMPORT_BYTES = 5 * 1024 * 1024


# --- Ticket integration -------------------------------------------------------

def can_select(user: User, ci: ConfigItem) -> bool:
    """Whether a user may raise a ticket against this item."""
    if ci.status == CIStatus.RETIRED:
        return False
    if user.is_staff:
        return True
    return (
        ci.assigned_user_id == user.id
        or ci.ci_type.is_shared_offering
        or (ci.ci_type == CIType.PRINTER and ci.assigned_user_id is None)
    )


def requester_choices(db: Session, user: User) -> dict[str, list[ConfigItem]]:
    """Items to offer on the new-ticket form, grouped for display."""
    active = ConfigItem.status != CIStatus.RETIRED
    mine = db.scalars(select(ConfigItem).where(active, ConfigItem.assigned_user_id == user.id).order_by(ConfigItem.name))
    offerings = db.scalars(
        select(ConfigItem)
        .where(active, ConfigItem.ci_type.in_([CIType.APPLICATION, CIType.SERVICE]))
        .order_by(ConfigItem.name)
    )
    printers = db.scalars(
        select(ConfigItem)
        .where(active, ConfigItem.ci_type == CIType.PRINTER, ConfigItem.assigned_user_id.is_(None))
        .order_by(ConfigItem.name)
    )
    choices = {
        "My devices": list(mine),
        "Applications & services": list(offerings),
        "Shared printers": list(printers),
    }
    if user.is_staff:
        seen = {ci.id for items in choices.values() for ci in items}
        others = db.scalars(select(ConfigItem).where(active).order_by(ConfigItem.name))
        choices["All other items"] = [ci for ci in others if ci.id not in seen]
    return {label: items for label, items in choices.items() if items}


def open_ticket_counts(db: Session) -> dict[int, int]:
    """Open tickets per staff member, for workload-aware suggestions."""
    open_statuses = [s for s in Status if s.is_open]
    return dict(
        db.execute(
            select(Ticket.assignee_id, func.count())
            .where(Ticket.status.in_(open_statuses), Ticket.assignee_id.is_not(None))
            .group_by(Ticket.assignee_id)
        ).all()
    )


def suggested_assignee(ticket: Ticket, workload: dict[int, int]) -> Optional[User]:
    """The least-busy active member of the affected item's support group."""
    group = ticket.ci.support_group if ticket.ci else None
    if group is None:
        return None
    candidates = [m for m in group.members if m.is_active and m.is_staff]
    if not candidates:
        return None
    return min(candidates, key=lambda m: (workload.get(m.id, 0), m.full_name))


# --- CSV import / export --------------------------------------------------------

@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    users_created: int = 0
    groups_created: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_enum(enum_cls, raw: str):
    value = raw.strip().lower()
    for member in enum_cls:
        if value in (member.value, member.label.lower(), member.value.replace("_", " ")):
            return member
    raise ValueError(f"unknown {enum_cls.__name__} '{raw}' (use one of: {', '.join(m.value for m in enum_cls)})")


def _parse_date(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date '{raw}' (use YYYY-MM-DD)")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")  # handles Excel's BOM
    except UnicodeDecodeError:
        return data.decode("cp1252")


def import_csv(db: Session, data: bytes, allow_sso_placeholders: bool) -> ImportResult:
    """Create or update items from CSV. Rows match on asset_tag, then serial_number,
    then name+type for applications/services. Blank cells never overwrite existing values.
    Caller commits."""
    result = ImportResult()
    reader = csv.DictReader(io.StringIO(_decode(data)))
    if not reader.fieldnames:
        result.errors.append("The file is empty.")
        return result
    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    if "name" not in headers or "type" not in headers:
        result.errors.append("The file must have at least 'name' and 'type' columns. Download the template.")
        return result

    groups = {g.name.lower(): g for g in db.scalars(select(SupportGroup))}

    for line, raw_row in enumerate(reader, start=2):
        row = {key: (raw_row.get(orig) or "").strip() for key, orig in headers.items()}
        if not any(row.values()):
            continue
        try:
            if not row.get("name"):
                raise ValueError("name is required")
            ci_type = _parse_enum(CIType, row["type"])
            status = _parse_enum(CIStatus, row["status"]) if row.get("status") else None
            warranty = _parse_date(row["warranty_expires"]) if row.get("warranty_expires") else None
        except ValueError as exc:
            result.errors.append(f"Row {line}: {exc}")
            continue

        try:
            with db.begin_nested():  # savepoint: a bad row never spoils the rest of the import
                outcome = _apply_row(db, row, ci_type, status, warranty, groups, allow_sso_placeholders)
        except IntegrityError:
            groups = {g.name.lower(): g for g in db.scalars(select(SupportGroup))}
            result.errors.append(f"Row {line}: conflicts with an existing item (duplicate value)")
            continue
        created, users_created, groups_created, warning = outcome
        result.created += created
        result.updated += not created
        result.users_created += users_created
        result.groups_created += groups_created
        if warning:
            result.warnings.append(f"Row {line}: {warning}")
    return result


def _apply_row(db, row, ci_type, status, warranty, groups, allow_sso_placeholders):
    """Create or update one item. Returns (created, users_created, groups_created, warning)."""
    ci = None
    if row.get("asset_tag"):
        ci = db.scalar(select(ConfigItem).where(ConfigItem.asset_tag == row["asset_tag"]))
    if ci is None and row.get("serial_number"):
        ci = db.scalar(select(ConfigItem).where(ConfigItem.serial_number == row["serial_number"]))
    if ci is None and not row.get("asset_tag") and not row.get("serial_number") and ci_type.is_shared_offering:
        ci = db.scalar(select(ConfigItem).where(ConfigItem.name == row["name"], ConfigItem.ci_type == ci_type))

    created = ci is None
    if created:
        ci = ConfigItem(name=row["name"], ci_type=ci_type, status=status or CIStatus.IN_USE)
        db.add(ci)
    ci.name, ci.ci_type = row["name"], ci_type
    if status:
        ci.status = status
    if warranty:
        ci.warranty_expires = warranty
    for attr in ("asset_tag", "serial_number", "manufacturer", "model", "version", "ip_address", "location",
                 "vendor", "notes"):
        if row.get(attr):
            setattr(ci, attr, row[attr])

    groups_created = 0
    if row.get("support_group"):
        key = row["support_group"].lower()
        if key not in groups:
            groups[key] = SupportGroup(name=row["support_group"])
            db.add(groups[key])
            groups_created = 1
        ci.support_group = groups[key]

    users_created, warning = 0, None
    if row.get("serial_number"):
        stmt = select(ConfigItem).where(ConfigItem.serial_number == row["serial_number"])
        if ci.id is not None:
            stmt = stmt.where(ConfigItem.id != ci.id)
        twin = db.scalar(stmt)
        if twin is not None:
            warning = f"serial {row['serial_number']} is also on {twin.display_name}; check for duplicates."
    email = row.get("assigned_to_email", "").lower()
    if email:
        owner = db.scalar(select(User).where(User.email == email))
        if owner is None and allow_sso_placeholders and "@" in email:
            # They are linked to this account when they first sign in with SSO.
            owner = User(email=email, full_name=email.split("@")[0], password_hash=NO_PASSWORD,
                         role=Role.REQUESTER, is_active=True)
            db.add(owner)
            users_created = 1
        if owner is None:
            warning = f"no user {email}; left unassigned."
        else:
            ci.assigned_user = owner
    db.flush()
    return created, users_created, groups_created, warning


def export_csv(db: Session) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for ci in db.scalars(select(ConfigItem).order_by(ConfigItem.ci_type, ConfigItem.name)):
        writer.writerow([
            ci.name, ci.ci_type.value, ci.status.value, ci.asset_tag or "", ci.serial_number or "",
            ci.manufacturer or "", ci.model or "", ci.version or "", ci.ip_address or "", ci.location or "",
            ci.vendor or "", ci.warranty_expires.isoformat() if ci.warranty_expires else "",
            ci.assigned_user.email if ci.assigned_user else "", ci.support_group.name if ci.support_group else "",
            ci.notes or "",
        ])
    return out.getvalue()


def template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    writer.writerow(["LT-0042", "laptop", "in_use", "LT-0042", "5CG1234XYZ", "Dell", "Latitude 7440", "Windows 11",
                     "", "HQ Floor 2", "Dell", "2027-06-30", "jane.doe@yourco.com", "Desktop Support", ""])
    writer.writerow(["Payroll (ADP)", "application", "in_use", "", "", "", "", "", "", "", "ADP", "", "",
                     "Business Apps", "Payroll system"])
    writer.writerow(["Core Firewall 1", "network", "in_use", "FW-01", "FGT60F0000", "Fortinet", "FortiGate 60F",
                     "7.2", "10.0.0.1", "Data Center", "Fortinet", "", "", "Network", ""])
    return out.getvalue()
