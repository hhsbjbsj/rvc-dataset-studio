"""SQLAlchemy database models and session management."""

import json
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    query = Column(String, nullable=False)
    query_type = Column(String, nullable=False)
    language = Column(String, default="auto")
    status = Column(String, default="queued")
    strategy = Column(String, nullable=True)
    source_urls = Column(Text, nullable=True)
    total_clips = Column(Integer, default=0)
    total_seconds = Column(Float, default=0.0)
    output_path = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)
    options_json = Column(Text, nullable=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    stages = relationship("JobStage", back_populates="job", cascade="all, delete-orphan")
    clips = relationship("Clip", back_populates="job", cascade="all, delete-orphan")

    def get_source_urls(self) -> list[str]:
        if not self.source_urls:
            return []
        return json.loads(self.source_urls)

    def set_source_urls(self, urls: list[str]) -> None:
        self.source_urls = json.dumps(urls)

    def get_metadata(self) -> dict[str, Any]:
        if not self.metadata_json:
            return {}
        return json.loads(self.metadata_json)

    def set_metadata(self, data: dict[str, Any]) -> None:
        self.metadata_json = json.dumps(data)

    def get_options(self) -> dict[str, Any]:
        if not self.options_json:
            return {}
        return json.loads(self.options_json)

    def set_options(self, data: dict[str, Any]) -> None:
        self.options_json = json.dumps(data)


class JobStage(Base):
    __tablename__ = "job_stages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    stage = Column(String, nullable=True)
    progress = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    ts = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="stages")


class Clip(Base):
    __tablename__ = "clips"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    filename = Column(String, nullable=True)
    duration_s = Column(Float, nullable=True)
    sample_rate = Column(Integer, nullable=True)
    snr_db = Column(Float, nullable=True)
    rms_db = Column(Float, nullable=True)
    transcript = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    speaker_id = Column(String, nullable=True)
    source_url = Column(Text, nullable=True)
    start_ts = Column(Float, nullable=True)
    end_ts = Column(Float, nullable=True)
    quality_score = Column(Float, nullable=True)
    kept = Column(Boolean, default=True)
    file_path = Column(Text, nullable=True)

    job = relationship("Job", back_populates="clips")


class SearchCache(Base):
    __tablename__ = "search_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(String, nullable=True)
    result = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    cached_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)


class RemovedHistoryEntry(Base):
    __tablename__ = "removed_history_entries"

    kind = Column(String, primary_key=True)
    project_id = Column(String, primary_key=True)
    item_id = Column(String, primary_key=True)


# SQLite needs check_same_thread=False for async usage
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    with engine.begin() as connection:
        Base.metadata.create_all(bind=connection)
        bundles = Base.metadata.tables.get("rvc_bundle_selections")
        if bundles is not None and "deselect_after_download" not in {
            column["name"] for column in inspect(connection).get_columns(bundles.name)
        }:
            # Before download badges, bundle records were only created for opt-in deselection.
            downloads = Base.metadata.tables["rvc_export_downloads"]
            saved = set(connection.execute(select(downloads.c.project_id, downloads.c.export_id)).tuples())
            completed = connection.scalars(select(bundles.c.exports).where(bundles.c.status == "completed"))
            restored = {(entry["project_id"], entry["export_id"]) for bundle in completed for entry in bundle} - saved
            if restored:
                connection.execute(downloads.insert(), [
                    {"project_id": project_id, "export_id": export_id} for project_id, export_id in restored
                ])
            connection.exec_driver_sql(
                "ALTER TABLE rvc_bundle_selections ADD COLUMN deselect_after_download BOOLEAN NOT NULL DEFAULT TRUE"
            )
    
    # Mark any jobs left in active/non-terminal states as failed since the backend restarted
    db = SessionLocal()
    try:
        from sqlalchemy import not_
        stale_jobs = db.query(Job).filter(not_(Job.status.in_(["done", "failed"]))).all()
        if stale_jobs:
            for job in stale_jobs:
                job.status = "failed"
                job.error_msg = "Job interrupted (server restarted or crashed)"
                logger.info("Marked stale job %s as failed on startup", job.id)
            db.commit()
    except Exception as e:
        logger.error("Failed to clean up stale jobs: %s", e)
    finally:
        db.close()

    # Clean up orphaned temp directories and output files from previously deleted jobs
    try:
        import shutil
        
        # Get list of all valid job IDs in the database
        db = SessionLocal()
        valid_job_ids = set(row[0] for row in db.query(Job.id).all())
        db.close()
        
        # Prune temp directory
        if settings.TEMP_DIR.exists():
            for p in settings.TEMP_DIR.iterdir():
                if p.is_dir() and p.name not in valid_job_ids:
                    # Avoid deleting other directories/files if they aren't job IDs
                    # We check if it is a valid UUID string
                    import uuid
                    try:
                        uuid.UUID(p.name)
                        shutil.rmtree(p, ignore_errors=True)
                        logger.info("Pruned orphaned temp directory: %s", p)
                    except ValueError:
                        # Also check if it is convert_{job_id}
                        if p.name.startswith("convert_"):
                            job_id = p.name.replace("convert_", "")
                            try:
                                uuid.UUID(job_id)
                                if job_id not in valid_job_ids:
                                    shutil.rmtree(p, ignore_errors=True)
                                    logger.info("Pruned orphaned convert temp directory: %s", p)
                            except ValueError:
                                pass
                            
        # Prune outputs directory
        if settings.OUTPUT_DIR.exists():
            for p in settings.OUTPUT_DIR.iterdir():
                # Any directory like dataset_<job_id>
                if p.is_dir() and p.name.startswith("dataset_"):
                    job_id = p.name.replace("dataset_", "")
                    if job_id not in valid_job_ids:
                        shutil.rmtree(p, ignore_errors=True)
                        logger.info("Pruned orphaned dataset directory: %s", p)
                # Any zip file like voiceforge_<job_id>.zip
                elif p.is_file() and p.name.startswith("voiceforge_") and p.name.endswith(".zip"):
                    job_id = p.name.replace("voiceforge_", "").replace(".zip", "")
                    if job_id not in valid_job_ids:
                        p.unlink()
                        logger.info("Pruned orphaned zip file: %s", p)
                # Any zip file like convert_<job_id>.zip
                elif p.is_file() and p.name.startswith("convert_") and p.name.endswith(".zip"):
                    job_id = p.name.replace("convert_", "").replace(".zip", "")
                    if job_id not in valid_job_ids:
                        p.unlink()
                        logger.info("Pruned orphaned convert zip file: %s", p)
    except Exception as e:
        logger.error("Failed to prune orphaned directories/files: %s", e)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    return SessionLocal()
