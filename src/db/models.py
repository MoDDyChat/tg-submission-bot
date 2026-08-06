"""SQLAlchemy models — single source of truth for the DB schema.

Used by Alembic for migrations and by the db.queries package for runtime
operations.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    is_moderator: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ban_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    submissions: Mapped[list["Submission"]] = relationship(back_populates="user")
    topic: Mapped["UserTopic | None"] = relationship(back_populates="user", uselist=False)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="'[]'::json")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    topic_card_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_media_message_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    # Digest of the card content last confirmed delivered to Telegram. NULL means
    # "never confirmed" — the reconcile pass treats that as drift and repaints.
    card_rendered_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="submissions")
    media: Mapped[list["SubmissionMedia"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan",
        order_by="SubmissionMedia.sort_order",
    )
    publication: Mapped["Publication | None"] = relationship(
        back_populates="submission", uselist=False, cascade="all, delete-orphan",
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_submissions_status", "status"),
        Index("idx_submissions_user_id", "user_id"),
    )


class SubmissionMedia(Base):
    __tablename__ = "submission_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False,
    )
    file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(256), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    submission: Mapped["Submission"] = relationship(back_populates="media")

    __table_args__ = (
        Index("idx_submission_media_submission_id", "submission_id"),
    )


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), unique=True, nullable=False,
    )
    edited_caption: Mapped[str | None] = mapped_column(Text)
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel_message_id: Mapped[int | None] = mapped_column(BigInteger)
    channel_message_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    submission: Mapped["Submission"] = relationship(back_populates="publication")

    __table_args__ = (
        Index(
            "idx_publications_publish_at",
            "publish_at",
            postgresql_where="published_at IS NULL",
        ),
        Index("idx_publications_submission_id", "submission_id"),
    )


class TagPresetSection(Base):
    __tablename__ = "tag_preset_sections"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    columns: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default="3",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    presets: Mapped[list["TagPresetEntry"]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TagPresetEntry.sort_order",
    )

    __table_args__ = (
        Index("idx_tag_preset_sections_sort", "sort_order"),
    )


class TagPresetEntry(Base):
    __tablename__ = "tag_presets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    preset_type: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tag_preset_sections.key", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    tag: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    section: Mapped[TagPresetSection] = relationship(back_populates="presets")

    __table_args__ = (
        Index("idx_tag_presets_type_sort", "preset_type", "sort_order"),
        UniqueConstraint("preset_type", "label", name="uq_tag_presets_type_label"),
        UniqueConstraint("preset_type", "tag", name="uq_tag_presets_type_tag"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False,
    )
    sender_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    submission: Mapped["Submission"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("idx_messages_submission_id", "submission_id"),
    )


class UserTopic(Base):
    __tablename__ = "user_topics"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    current_status_key: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    title_sync_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    title_applied_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    title_force_sync_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="topic")

    __table_args__ = (
        UniqueConstraint("topic_id", name="uq_user_topics_topic_id"),
        Index(
            "idx_user_topics_title_sync_pending",
            "updated_at",
            postgresql_where=(title_sync_version > title_applied_version),
        ),
    )


class EditLock(Base):
    __tablename__ = "edit_locks"

    resource_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    moderator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acquired_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_edit_locks_expires_at", "expires_at"),
    )


class SystemMessage(Base):
    """Stores message_id for idempotent system messages (legend, queue, etc.)."""

    __tablename__ = "system_messages"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
