"""ORM models: users, tickets, comments, activity history and email log."""

import enum
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, Date, DateTime, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, enum.Enum):
    REQUESTER = "requester"
    AGENT = "agent"        # service desk technician
    MANAGER = "manager"    # IT manager - triages and assigns

    @property
    def label(self) -> str:
        return {"requester": "Requester", "agent": "Service Desk Agent", "manager": "IT Manager"}[self.value]


class Category(str, enum.Enum):
    GENERAL = "general"
    INCIDENT = "incident"
    SOFTWARE = "software"
    HARDWARE = "hardware"
    FIREWALL = "firewall"
    NETWORK = "network"
    ACCOUNT = "account"
    EMAIL = "email"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            "general": "General Inquiry",
            "incident": "Incident (something is broken)",
            "software": "Software Request / Issue",
            "hardware": "Hardware Request / Issue",
            "firewall": "Firewall Change Request",
            "network": "Network / VPN",
            "account": "Account / Access Request",
            "email": "Email",
            "other": "Other",
        }[self.value]


class Priority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def label(self) -> str:
        return self.value.title()


class Status(str, enum.Enum):
    NEW = "new"                  # waiting for the IT manager to assign
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    PENDING_CHANGE = "pending_change"  # waiting on a change request (set automatically)
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()

    @property
    def is_open(self) -> bool:
        return self not in (Status.RESOLVED, Status.CLOSED, Status.CANCELLED)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # SSO-only accounts store an unusable marker (see security.NO_PASSWORD).
    password_hash: Mapped[str] = mapped_column(String(255))
    sso_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    sso_subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.REQUESTER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Member of the change advisory board (IT managers can always approve changes).
    is_change_approver: Mapped[bool] = mapped_column(Boolean, default=False)
    # AbelOrder: who approves this person's purchase requests, their department, and
    # whether they work in Procurement (see all requests, issue PO numbers).
    supervisor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), nullable=True)
    is_procurement: Mapped[bool] = mapped_column(Boolean, default=False)
    # May email service notices to the distribution lists (IT managers always can).
    can_send_notices: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    supervisor: Mapped[Optional["User"]] = relationship(remote_side="User.id", foreign_keys=[supervisor_id])
    department_ref: Mapped[Optional["Department"]] = relationship(foreign_keys=[department_id])

    @property
    def may_send_notices(self) -> bool:
        return self.is_active and (self.role == Role.MANAGER or (self.is_staff and self.can_send_notices))

    @property
    def can_approve_changes(self) -> bool:
        return self.is_active and (self.role == Role.MANAGER or (self.is_staff and self.is_change_approver))

    @property
    def is_staff(self) -> bool:
        return self.role in (Role.AGENT, Role.MANAGER)

    @property
    def has_password(self) -> bool:
        return not self.password_hash.startswith("!")


class CIType(str, enum.Enum):
    LAPTOP = "laptop"
    DESKTOP = "desktop"
    MOBILE = "mobile"
    MONITOR = "monitor"
    PERIPHERAL = "peripheral"
    PRINTER = "printer"
    SERVER = "server"
    NETWORK = "network"
    APPLICATION = "application"
    SERVICE = "service"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            "laptop": "Laptop",
            "desktop": "Desktop",
            "mobile": "Mobile Device",
            "monitor": "Monitor",
            "peripheral": "Peripheral",
            "printer": "Printer",
            "server": "Server",
            "network": "Network Device",
            "application": "Application",
            "service": "Business Service",
            "other": "Other",
        }[self.value]

    @property
    def is_shared_offering(self) -> bool:
        """Applications and services any requester can raise tickets against."""
        return self in (CIType.APPLICATION, CIType.SERVICE)


class CIStatus(str, enum.Enum):
    IN_USE = "in_use"
    IN_STOCK = "in_stock"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"

    @property
    def label(self) -> str:
        return {"in_use": "In Use", "in_stock": "In Stock", "maintenance": "In Maintenance", "retired": "Retired"}[
            self.value
        ]


support_group_members = Table(
    "support_group_members",
    Base.metadata,
    Column("group_id", ForeignKey("support_groups.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class SupportGroup(Base):
    """A team responsible for a set of configuration items (e.g. Network, Apps, Desktop Support)."""

    __tablename__ = "support_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Assign new tickets for this group's items to the least-busy member automatically.
    auto_assign: Mapped[bool] = mapped_column(Boolean, default=False)

    members: Mapped[list[User]] = relationship(secondary=support_group_members, order_by=User.full_name)


class ConfigItem(Base):
    """A CMDB configuration item: a device, application or service people can need help with."""

    __tablename__ = "config_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    ci_type: Mapped[CIType] = mapped_column(Enum(CIType), index=True)
    status: Mapped[CIStatus] = mapped_column(Enum(CIStatus), default=CIStatus.IN_USE, index=True)
    asset_tag: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    serial_number: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # OS or software version
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    warranty_expires: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Tickets against this item can't be resolved without an approved, completed change.
    change_controlled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Set for devices synced from Entra ID (the device's directory object ID).
    entra_object_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    entra_last_sign_in: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    entra_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    assigned_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    support_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("support_groups.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    assigned_user: Mapped[Optional[User]] = relationship()
    support_group: Mapped[Optional[SupportGroup]] = relationship()

    @property
    def display_name(self) -> str:
        return f"{self.name} [{self.asset_tag}]" if self.asset_tag else self.name

    @property
    def warranty_expired(self) -> bool:
        return self.warranty_expires is not None and self.warranty_expires < date.today()


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True, default="")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[Category] = mapped_column(Enum(Category))
    priority: Mapped[Priority] = mapped_column(Enum(Priority), default=Priority.MEDIUM)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.NEW, index=True)
    # Category-specific details (e.g. firewall source/destination/port, software name).
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    # The affected product / device from the CMDB, if the requester picked one.
    ci_id: Mapped[Optional[int]] = mapped_column(ForeignKey("config_items.id"), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    assignee: Mapped[Optional[User]] = relationship(foreign_keys=[assignee_id])
    assigned_by: Mapped[Optional[User]] = relationship(foreign_keys=[assigned_by_id])
    ci: Mapped[Optional[ConfigItem]] = relationship()
    changes: Mapped[list["ChangeRequest"]] = relationship(back_populates="ticket", order_by="ChangeRequest.id")
    comments: Mapped[list["Comment"]] = relationship(
        back_populates="ticket", order_by="Comment.created_at", cascade="all, delete-orphan"
    )
    activities: Mapped[list["Activity"]] = relationship(
        back_populates="ticket", order_by="Activity.created_at", cascade="all, delete-orphan"
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    # Internal work notes are only visible to IT staff.
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="comments")
    author: Mapped[User] = relationship()


class Activity(Base):
    """Audit trail of changes on a ticket."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    message: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="activities")
    actor: Mapped[Optional[User]] = relationship()


class EmailLog(Base):
    """Every notification the system tried to send, and whether it went out."""

    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    to_address: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|logged|failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON list of {"path", "filename", "content_type"} files to attach.
    attachments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def attachment_list(self) -> list[dict]:
        import json
        return json.loads(self.attachments) if self.attachments else []


class SyncRun(Base):
    """One run of an external inventory sync (e.g. Entra devices)."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    trigger: Mapped[str] = mapped_column(String(20))  # schedule | manual | cli
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | success | failed
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AppSetting(Base):
    """Small key/value store for settings managers edit in the UI."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class CIRelationship(Base):
    """'source depends on target', e.g. Payroll app -> SQL01 server -> Core switch."""

    __tablename__ = "ci_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("config_items.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("config_items.id", ondelete="CASCADE"), index=True)

    source: Mapped[ConfigItem] = relationship(foreign_keys=[source_id])
    target: Mapped[ConfigItem] = relationship(foreign_keys=[target_id])


class ChangeType(str, enum.Enum):
    STANDARD = "standard"    # pre-approved, routine
    NORMAL = "normal"        # needs approval
    EMERGENCY = "emergency"  # expedited: one approval

    @property
    def label(self) -> str:
        return self.value.title()


class ChangeState(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"        # scheduled, waiting for the window
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"  # being implemented
    COMPLETED = "completed"      # implemented successfully
    FAILED = "failed"            # implementation failed / backed out
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()

    @property
    def is_active(self) -> bool:
        return self in (ChangeState.DRAFT, ChangeState.PENDING_APPROVAL, ChangeState.APPROVED, ChangeState.IN_PROGRESS)


class Level(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def label(self) -> str:
        return self.value.title()


change_items = Table(
    "change_items",
    Base.metadata,
    Column("change_id", ForeignKey("change_requests.id", ondelete="CASCADE"), primary_key=True),
    Column("ci_id", ForeignKey("config_items.id", ondelete="CASCADE"), primary_key=True),
)


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True, default="")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    justification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_type: Mapped[ChangeType] = mapped_column(Enum(ChangeType), default=ChangeType.NORMAL)
    state: Mapped[ChangeState] = mapped_column(Enum(ChangeState), default=ChangeState.DRAFT, index=True)
    risk: Mapped[Level] = mapped_column(Enum(Level), default=Level.MEDIUM)
    impact: Mapped[Level] = mapped_column(Enum(Level), default=Level.MEDIUM)
    implementation_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    backout_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Wall-clock times in the organisation's local time zone, as entered.
    planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    planned_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approvals_required: Mapped[int] = mapped_column(Integer, default=1)
    close_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    implementer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    ticket: Mapped[Optional[Ticket]] = relationship(back_populates="changes")
    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    implementer: Mapped[Optional[User]] = relationship(foreign_keys=[implementer_id])
    items: Mapped[list[ConfigItem]] = relationship(secondary=change_items, order_by=ConfigItem.name)
    approvals: Mapped[list["ChangeApproval"]] = relationship(
        back_populates="change", order_by="ChangeApproval.created_at", cascade="all, delete-orphan"
    )
    activities: Mapped[list["ChangeActivity"]] = relationship(
        back_populates="change", order_by="ChangeActivity.created_at", cascade="all, delete-orphan"
    )

    @property
    def approve_count(self) -> int:
        return sum(1 for a in self.approvals if a.approved and a.current)


class ChangeApproval(Base):
    __tablename__ = "change_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("change_requests.id", ondelete="CASCADE"), index=True)
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # False once the change is sent back to draft and resubmitted (votes don't carry over).
    current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    change: Mapped[ChangeRequest] = relationship(back_populates="approvals")
    approver: Mapped[User] = relationship()


class ChangeActivity(Base):
    __tablename__ = "change_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("change_requests.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    message: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    change: Mapped[ChangeRequest] = relationship(back_populates="activities")
    actor: Mapped[Optional[User]] = relationship()


# --- AbelOrder (purchase requests) -----------------------------------------------------

class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    head_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    head: Mapped[Optional[User]] = relationship(foreign_keys=[head_id])


class Project(Base):
    """Project / work-order codes employees can charge purchase requests to."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    contact_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Urgency(str, enum.Enum):
    NORMAL = "normal"
    URGENT = "urgent"
    CRITICAL = "critical"

    @property
    def label(self) -> str:
        return self.value.title()


class PRStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"        # all signatures done, sent to Procurement, awaiting PO number
    PO_ISSUED = "po_issued"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {"po_issued": "PO Issued", "approved": "Approved - Awaiting PO"}.get(
            self.value, self.value.replace("_", " ").title())


class ApprovalRole(str, enum.Enum):
    SUPERVISOR = "supervisor"
    DEPT_HEAD = "dept_head"
    CSO = "cso"

    @property
    def label(self) -> str:
        return {"supervisor": "Direct Supervisor", "dept_head": "Department Head", "cso": "CSO"}[self.value]


class StepState(str, enum.Enum):
    WAITING = "waiting"    # earlier step not signed yet
    PENDING = "pending"    # with this approver now
    APPROVED = "approved"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return self.value.title()


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True, default="")
    status: Mapped[PRStatus] = mapped_column(Enum(PRStatus), default=PRStatus.DRAFT, index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    requester_name: Mapped[str] = mapped_column(String(255))
    request_date: Mapped[date] = mapped_column(Date)
    justification: Mapped[str] = mapped_column(Text)
    urgency: Mapped[Urgency] = mapped_column(Enum(Urgency), default=Urgency.NORMAL)
    needed_by: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    project_number: Mapped[str] = mapped_column(String(100))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), nullable=True)
    supervisor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    # True when the supervisor came from the user's profile (set by an administrator).
    supervisor_from_profile: Mapped[bool] = mapped_column(Boolean, default=False)

    vendor_is_new: Mapped[bool] = mapped_column(Boolean, default=False)
    vendor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    vendor_name: Mapped[str] = mapped_column(String(255))
    vendor_contact_name: Mapped[str] = mapped_column(String(255))
    vendor_contact_email: Mapped[str] = mapped_column(String(255))

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Money is kept as exact decimal strings; total_cents allows sorting/filtering.
    total: Mapped[str] = mapped_column(String(32), default="0.00")
    total_cents: Mapped[int] = mapped_column(Integer, default=0, index=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    po_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    po_issued_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    supervisor: Mapped[Optional[User]] = relationship(foreign_keys=[supervisor_id])
    department: Mapped[Optional[Department]] = relationship()
    vendor: Mapped[Optional[Vendor]] = relationship()
    po_issued_by: Mapped[Optional[User]] = relationship(foreign_keys=[po_issued_by_id])
    items: Mapped[list["PurchaseItem"]] = relationship(
        back_populates="request", order_by="PurchaseItem.line_no", cascade="all, delete-orphan"
    )
    approvals: Mapped[list["PurchaseApproval"]] = relationship(
        back_populates="request", order_by="PurchaseApproval.step_order", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["PurchaseAttachment"]] = relationship(
        back_populates="request", order_by="PurchaseAttachment.id", cascade="all, delete-orphan"
    )
    activities: Mapped[list["PurchaseActivity"]] = relationship(
        back_populates="request", order_by="PurchaseActivity.id", cascade="all, delete-orphan"
    )

    @property
    def current_step(self) -> Optional["PurchaseApproval"]:
        return next((a for a in self.approvals if a.state == StepState.PENDING), None)


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[str] = mapped_column(String(32))
    uom: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    catalog_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # RMS / catalog #
    unit_cost: Mapped[str] = mapped_column(String(32))  # cost per UOM
    line_total: Mapped[str] = mapped_column(String(32))

    request: Mapped[PurchaseRequest] = relationship(back_populates="items")


class PurchaseApproval(Base):
    __tablename__ = "purchase_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True)
    step_order: Mapped[int] = mapped_column(Integer)
    role: Mapped[ApprovalRole] = mapped_column(Enum(ApprovalRole))
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    state: Mapped[StepState] = mapped_column(Enum(StepState), default=StepState.WAITING)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # e.g. "escalated: requester is supervisor"
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    request: Mapped[PurchaseRequest] = relationship(back_populates="approvals")
    approver: Mapped[User] = relationship()


class PurchaseAttachment(Base):
    __tablename__ = "purchase_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))       # original name, for display
    stored_name: Mapped[str] = mapped_column(String(255))    # random name on disk
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    request: Mapped[PurchaseRequest] = relationship(back_populates="attachments")
    uploaded_by: Mapped[User] = relationship()


class PurchaseActivity(Base):
    __tablename__ = "purchase_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    message: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    request: Mapped[PurchaseRequest] = relationship(back_populates="activities")
    actor: Mapped[Optional[User]] = relationship()


# --- Service notices (outage / maintenance / change announcements) ---------------------------

class DistributionList(Base):
    """A mail distribution list address (e.g. allstaff@company.com); the mail server expands it."""

    __tablename__ = "distribution_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # pre-selected on new notices
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class NoticeKind(str, enum.Enum):
    OUTAGE = "outage"
    MAINTENANCE = "maintenance"
    CHANGE = "change"
    INFO = "info"

    @property
    def label(self) -> str:
        return {"outage": "Outage / Major Incident", "maintenance": "Planned Maintenance",
                "change": "Service Change / Update", "info": "Information"}[self.value]

    @property
    def tag(self) -> str:
        return {"outage": "OUTAGE", "maintenance": "PLANNED MAINTENANCE", "change": "SERVICE CHANGE",
                "info": "IT NOTICE"}[self.value]


class NoticeSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def label(self) -> str:
        return {"info": "Low - informational", "warning": "Medium - some users affected",
                "critical": "High - major impact"}[self.value]


class NoticeStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"        # sent; shown on the banner / status page
    RESOLVED = "resolved"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {"active": "Active (sent)"}.get(self.value, self.value.title())


notice_lists = Table(
    "notice_lists",
    Base.metadata,
    Column("notice_id", ForeignKey("service_notices.id", ondelete="CASCADE"), primary_key=True),
    Column("list_id", ForeignKey("distribution_lists.id", ondelete="CASCADE"), primary_key=True),
)

notice_items = Table(
    "notice_items",
    Base.metadata,
    Column("notice_id", ForeignKey("service_notices.id", ondelete="CASCADE"), primary_key=True),
    Column("ci_id", ForeignKey("config_items.id", ondelete="CASCADE"), primary_key=True),
)


class ServiceNotice(Base):
    __tablename__ = "service_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True, default="")
    kind: Mapped[NoticeKind] = mapped_column(Enum(NoticeKind))
    severity: Mapped[NoticeSeverity] = mapped_column(Enum(NoticeSeverity), default=NoticeSeverity.WARNING)
    status: Mapped[NoticeStatus] = mapped_column(Enum(NoticeStatus), default=NoticeStatus.DRAFT, index=True)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)                       # what is happening
    impact: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # who/what is affected
    user_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # what users should do
    # Local wall-clock times, as entered (same convention as change windows).
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    show_banner: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_recipients: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # comma-separated addresses

    change_id: Mapped[Optional[int]] = mapped_column(ForeignKey("change_requests.id"), nullable=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sent_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    change: Mapped[Optional[ChangeRequest]] = relationship()
    ticket: Mapped[Optional[Ticket]] = relationship()
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    sent_by: Mapped[Optional[User]] = relationship(foreign_keys=[sent_by_id])
    lists: Mapped[list[DistributionList]] = relationship(secondary=notice_lists, order_by=DistributionList.name)
    items: Mapped[list[ConfigItem]] = relationship(secondary=notice_items, order_by=ConfigItem.name)
    updates: Mapped[list["NoticeUpdate"]] = relationship(
        back_populates="notice", order_by="NoticeUpdate.id", cascade="all, delete-orphan"
    )


class NoticeUpdate(Base):
    """A follow-up sent to the same lists: progress update, resolution or cancellation."""

    __tablename__ = "notice_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("service_notices.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # sent | update | resolved | cancelled
    message: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    notice: Mapped[ServiceNotice] = relationship(back_populates="updates")
    author: Mapped[User] = relationship()
