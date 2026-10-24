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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
