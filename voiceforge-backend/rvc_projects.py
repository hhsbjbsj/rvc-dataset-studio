"""Persisted project and media state, independent of the legacy Celery queue."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from config import get_settings, rvc_export_root
from database import Base


def new_id() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


class RvcProject(Base):
    __tablename__ = "rvc_projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    series: Mapped[str] = mapped_column(String(120))
    character: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(40))
    target_minutes: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RvcWorkspace(Base):
    __tablename__ = "rvc_workspace"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("rvc_projects.id"))


class RvcExportSelection(Base):
    __tablename__ = "rvc_export_selections"

    project_id: Mapped[str] = mapped_column(ForeignKey("rvc_projects.id"), primary_key=True)
    export_id: Mapped[str] = mapped_column(String(32), primary_key=True)


class RvcSource(Base):
    __tablename__ = "rvc_sources"
    __table_args__ = (UniqueConstraint("project_id", "source_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("rvc_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    source_key: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    uploader: Mapped[str] = mapped_column(Text, default="")
    media: Mapped[dict] = mapped_column(JSON, default=dict)
    track_index: Mapped[int | None] = mapped_column(Integer, default=None)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    stage: Mapped[str] = mapped_column(String(20), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    duplicate_clips: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RvcClip(Base):
    __tablename__ = "rvc_clips"
    __table_args__ = (UniqueConstraint("project_id", "digest"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("rvc_projects.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("rvc_sources.id"), index=True)
    filename: Mapped[str] = mapped_column(String(16))
    generation: Mapped[str] = mapped_column(String(32))
    digest: Mapped[str] = mapped_column(String(64))
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    quality: Mapped[dict] = mapped_column(JSON)
    review: Mapped[str] = mapped_column(String(12), default="pending")


class RvcMergeBuffer(Base):
    __tablename__ = "rvc_merge_buffer"

    clip_id: Mapped[str] = mapped_column(ForeignKey("rvc_clips.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def active_clip():
    return RvcClip.id.not_in(select(RvcMergeBuffer.clip_id))


def project_data(project: RvcProject) -> dict:
    return {key: getattr(project, key) for key in ("id", "series", "character", "language", "target_minutes", "created_at")}


def source_data(source: RvcSource) -> dict:
    return {key: getattr(source, key) for key in (
        "id", "kind", "origin", "title", "uploader", "media", "track_index", "status",
        "stage", "progress", "error", "duplicate_clips", "created_at", "options",
    )}


def clip_data(clip: RvcClip, source: RvcSource) -> dict:
    data = {key: getattr(clip, key) for key in ("id", "source_id", "start", "end", "duration", "quality", "review")}
    return {**data, "source_title": source.title, "source_kind": source.kind, "origin": source.origin}


def work_dir(project_id: str, source_id: str) -> Path:
    return get_settings().TEMP_DIR / "rvc" / project_id / source_id


def clip_path(clip: RvcClip) -> Path:
    return work_dir(clip.project_id, clip.source_id) / clip.generation / clip.filename


def export_root(project_id: str) -> Path:
    root = rvc_export_root().resolve()
    legacy = root / project_id
    if legacy.exists() or not root.is_dir():
        return legacy
    suffix = f" [{project_id}]"
    return next((path for path in root.iterdir()
                 if path.name.endswith(suffix) and path.is_dir() and path.resolve() == path), legacy)
