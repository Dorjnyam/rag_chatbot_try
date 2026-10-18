"""Metadata only: vectors live in Chroma, extraction artifacts on disk."""
from datetime import datetime, timezone
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from .extensions import db


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Document(db.Model):
    __tablename__ = "document"
    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(sa.String(240))
    language: Mapped[str] = mapped_column(sa.String(10), default="mixed")
    state: Mapped[str] = mapped_column(sa.String(20), default="active")
    active_version_id: Mapped[str | None] = mapped_column(sa.String(36))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)
    __table_args__ = (
        sa.ForeignKeyConstraint(["id", "active_version_id"], ["document_version.document_id", "document_version.id"], name="fk_document_active_own_version", use_alter=True),
        sa.CheckConstraint("language IN ('mn','en','mixed')", name="ck_document_language"),
        sa.CheckConstraint("state IN ('active','deleting','deleted')", name="ck_document_state"),
    )


class DocumentVersion(db.Model):
    __tablename__ = "document_version"
    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(sa.ForeignKey("document.id"), index=True)
    version_number: Mapped[int] = mapped_column(sa.Integer)
    original_filename: Mapped[str] = mapped_column(sa.String(255))
    media_type: Mapped[str] = mapped_column(sa.String(10))
    sha256: Mapped[str] = mapped_column(sa.String(64), index=True)
    file_path: Mapped[str] = mapped_column(sa.Text)
    extraction_path: Mapped[str | None] = mapped_column(sa.Text)
    manifest_path: Mapped[str | None] = mapped_column(sa.Text)
    state: Mapped[str] = mapped_column(sa.String(24), default="pending")
    warnings: Mapped[list] = mapped_column(sa.JSON, default=list)
    error: Mapped[str | None] = mapped_column(sa.Text)
    config: Mapped[dict] = mapped_column(sa.JSON)
    config_hash: Mapped[str] = mapped_column(sa.String(64))
    collection_name: Mapped[str] = mapped_column(sa.String(80))
    expected_chunks: Mapped[int] = mapped_column(sa.Integer, default=0)
    review_required: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        sa.UniqueConstraint("document_id", "id", name="uq_document_version_owner"),
        sa.CheckConstraint("state IN ('pending','parsing','review','indexing','ready','failed','deleted')", name="ck_version_state"),
    )


class QueryLog(db.Model):
    __tablename__ = "query_log"
    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    question: Mapped[str] = mapped_column(sa.Text)
    language: Mapped[str] = mapped_column(sa.String(10))
    answer: Mapped[str] = mapped_column(sa.Text, default="")
    status: Mapped[str] = mapped_column(sa.String(32), default="retrieved")
    config: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    trace: Mapped[list] = mapped_column(sa.JSON, default=list)
    citations: Mapped[list] = mapped_column(sa.JSON, default=list)
    timings: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    usage: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)


class EvaluationQuestion(db.Model):
    __tablename__ = "evaluation_question"
    id: Mapped[str] = mapped_column(sa.String(80), primary_key=True)
    revision: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(sa.JSON)


class EvaluationRun(db.Model):
    __tablename__ = "evaluation_run"
    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=uid)
    snapshot: Mapped[dict] = mapped_column(sa.JSON)
    config: Mapped[dict] = mapped_column(sa.JSON)
    metrics: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class EvaluationResult(db.Model):
    __tablename__ = "evaluation_result"
    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("evaluation_run.id"), index=True)
    question_id: Mapped[str] = mapped_column(sa.String(80))
    question_revision: Mapped[str] = mapped_column(sa.String(64))
    query_log_id: Mapped[str] = mapped_column(sa.ForeignKey("query_log.id"))
    metrics: Mapped[dict] = mapped_column(sa.JSON)
    review: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    __table_args__ = (sa.ForeignKeyConstraint(["question_id", "question_revision"], ["evaluation_question.id", "evaluation_question.revision"], name="fk_evaluation_question_revision"),)
