from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("channel", "external_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    external_key: Mapped[str] = mapped_column(String(255))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_address: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(30), default="inquiry")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    active_classification: Mapped[str | None] = mapped_column(String(24), index=True)
    # Stored as an identifier without a foreign key because Message already points back
    # to Conversation; a second FK would make SQLAlchemy's messages relationship ambiguous.
    active_classification_message_id: Mapped[int | None] = mapped_column(Integer)
    classification_open: Mapped[bool] = mapped_column(Boolean, default=False)
    classification_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification_resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    last_inbound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    overdue_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.id"
    )
    drafts: Mapped[list[Draft]] = relationship(back_populates="conversation", order_by="Draft.id")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("channel", "provider_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    provider_id: Mapped[str] = mapped_column(String(500))
    direction: Mapped[str] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(500))
    sender: Mapped[str | None] = mapped_column(String(255))
    recipient: Mapped[str | None] = mapped_column(String(255))
    reply_to_id: Mapped[str | None] = mapped_column(String(500))
    references: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    classification_label: Mapped[str | None] = mapped_column(String(24), index=True)
    classification_confidence: Mapped[str | None] = mapped_column(String(12))
    classification_reason: Mapped[str | None] = mapped_column(String(300))
    classification_data: Mapped[dict | None] = mapped_column(JSON)
    classification_override: Mapped[str | None] = mapped_column(String(24))
    classification_model: Mapped[str | None] = mapped_column(String(100))
    classification_reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    classification_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), unique=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_id: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    conversation: Mapped[Conversation] = relationship(back_populates="drafts")


class BookingTask(Base):
    __tablename__ = "booking_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), unique=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset: Mapped[str] = mapped_column(String(20), default="availability", index=True)
    source: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    parser_version: Mapped[str | None] = mapped_column(String(20))
    warnings: Mapped[list | None] = mapped_column(JSON, default=list)
    changes: Mapped[list | None] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PropertySnapshot(Base):
    __tablename__ = "property_snapshots"
    __table_args__ = (UniqueConstraint("batch_id", "property_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    row_hash: Mapped[str] = mapped_column(String(64))
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    status_original: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean)
    category: Mapped[str] = mapped_column(String(100))
    capacity: Mapped[int] = mapped_column(Integer)
    building: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(String(255))


class ReservationSnapshot(Base):
    __tablename__ = "reservation_snapshots"
    __table_args__ = (UniqueConstraint("batch_id", "locator"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    source_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    source_row: Mapped[int] = mapped_column(Integer)
    row_hash: Mapped[str] = mapped_column(String(64))
    locator: Mapped[str] = mapped_column(String(150), index=True)
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    status_original: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    created_on: Mapped[date | None] = mapped_column(Date)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    nights: Mapped[int] = mapped_column(Integer)
    adults: Mapped[int] = mapped_column(Integer)
    children: Mapped[int] = mapped_column(Integer)
    infants: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(255))
    portal: Mapped[str | None] = mapped_column(String(100))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    net: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    pending: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    property_name: Mapped[str] = mapped_column(String(255))
    capacity: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(500), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MailCursor(Base):
    __tablename__ = "mail_cursors"
    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox: Mapped[str] = mapped_column(String(255), unique=True)
    uidvalidity: Mapped[int] = mapped_column(Integer)
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ReservationSyncState(Base):
    __tablename__ = "reservation_sync_states"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="idle")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    interval_hours: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class TariffSeason(Base):
    __tablename__ = "tariff_seasons"
    __table_args__ = (
        UniqueConstraint(
            "tariff_name", "rate_plan", "start_date", name="uq_tariff_seasons_name_plan_start"
        ),
        CheckConstraint("end_date >= start_date", name="ck_tariff_seasons_valid_range"),
        CheckConstraint("nightly_rate >= 0", name="ck_tariff_seasons_nonnegative_rate"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tariff_name: Mapped[str] = mapped_column(String(180), index=True)
    rate_plan: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    nightly_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2))
