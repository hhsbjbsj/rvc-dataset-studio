"""Job CRUD REST endpoints."""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import Clip, Job, JobStage, get_db
from schemas import (
    CreateJobRequest,
    CreateJobResponse,
    ExportRequest,
    JobResponse,
    JobStageResponse,
    PaginatedClipsResponse,
    ClipResponse,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job_to_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        query=job.query,
        query_type=job.query_type,
        language=job.language,
        status=job.status,
        strategy=job.strategy,
        source_urls=job.get_source_urls(),
        total_clips=job.total_clips,
        total_seconds=job.total_seconds,
        output_path=job.output_path,
        metadata=job.get_metadata(),
        options=job.get_options(),
        error_msg=job.error_msg,
        created_at=job.created_at,
        updated_at=job.updated_at,
        stages=[JobStageResponse.model_validate(s) for s in job.stages],
    )


@router.post("/create", response_model=CreateJobResponse)
def create_job(request: CreateJobRequest, db: Session = Depends(get_db)):
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        query=request.query,
        query_type=request.query_type,
        language=request.language,
        status="queued",
    )
    job.set_options(request.options.model_dump())
    db.add(job)
    db.commit()

    # Estimate time based on target duration
    target_min = request.options.target_duration_minutes
    estimated = max(5, int(target_min * 0.4))

    try:
        from workers.pipeline import run_pipeline

        run_pipeline.apply_async(args=[job_id], task_id=job_id, retry=False)
    except Exception as e:  # broker (Redis) not reachable
        job.status = "failed"
        job.error_msg = "Job queue unavailable: is Redis running?"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="The job queue (Redis) is not running. Start Redis, then try again.",
        ) from e

    return CreateJobResponse(
        job_id=job_id,
        status="queued",
        estimated_time_minutes=estimated,
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_response(job)


@router.get("/{job_id}/clips", response_model=PaginatedClipsResponse)
def get_job_clips(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    kept_only: bool = True,
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    q = db.query(Clip).filter(Clip.job_id == job_id)
    if kept_only:
        q = q.filter(Clip.kept == True)  # noqa: E712

    total = q.count()
    clips = (
        q.order_by(Clip.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PaginatedClipsResponse(
        clips=[ClipResponse.model_validate(c) for c in clips],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/{job_id}/clips/{clip_id}")
def remove_clip(job_id: str, clip_id: int, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id, Clip.job_id == job_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip.kept = False
    db.commit()
    return {"status": "removed", "clip_id": clip_id}


@router.post("/{job_id}/clips/{clip_id}/restore")
def restore_clip(job_id: str, clip_id: int, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id, Clip.job_id == job_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip.kept = True
    db.commit()
    return {"status": "restored", "clip_id": clip_id}


@router.post("/{job_id}/export")
def export_job(job_id: str, request: ExportRequest = ExportRequest(), db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from workers.stage_package import package_dataset

    options = job.get_options()
    if request.output_format:
        options["output_format"] = request.output_format
    if request.quality_threshold is not None:
        options["quality_threshold"] = request.quality_threshold

    package_dataset.delay(job_id, options)
    return {"status": "packaging", "job_id": job_id}


@router.get("/{job_id}/download")
def download_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.output_path:
        raise HTTPException(status_code=404, detail="Dataset not ready for download")

    from pathlib import Path

    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"voiceforge_{job.query.replace(' ', '_')}_{job_id[:8]}.zip",
    )


@router.delete("/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    import shutil
    import logging
    from pathlib import Path
    from config import get_settings

    logger = logging.getLogger(__name__)
    settings = get_settings()

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Delete the packaged dataset zip file
    if job.output_path:
        try:
            zip_file = Path(job.output_path)
            if zip_file.exists():
                zip_file.unlink()
                logger.info("Deleted output zip file for job %s: %s", job_id, zip_file)
        except Exception as e:
            logger.error("Failed to delete packaged zip for job %s: %s", job_id, e)

    # 2. Delete temporary work directory (downloads, converted, segmented, etc.)
    try:
        work_dir = settings.TEMP_DIR / job_id
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
            logger.info("Deleted temporary work directory for job %s: %s", job_id, work_dir)

        convert_work_dir = settings.TEMP_DIR / f"convert_{job_id}"
        if convert_work_dir.exists():
            shutil.rmtree(convert_work_dir, ignore_errors=True)
            logger.info("Deleted temporary convert work directory for job %s: %s", job_id, convert_work_dir)

        dataset_dir = settings.OUTPUT_DIR / f"dataset_{job_id}"
        if dataset_dir.exists():
            shutil.rmtree(dataset_dir, ignore_errors=True)
            logger.info("Deleted dataset directory for job %s: %s", job_id, dataset_dir)
    except Exception as e:
        logger.error("Failed to clean up directories for job %s: %s", job_id, e)

    db.delete(job)
    db.commit()
    return {"status": "deleted", "job_id": job_id}


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    import shutil
    import logging
    from workers.celery_app import redis_client
    from workers.progress import publish_progress
    from config import get_settings

    logger = logging.getLogger(__name__)
    settings = get_settings()

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in ("done", "failed"):
        return {"status": "already_completed", "job_id": job_id}

    # 1. Set the cancelled flag in Redis to trigger cooperative cancellation
    try:
        redis_client.set(f"job:{job_id}:cancelled", "1")
    except Exception as e:
        logger.error("Failed to set cancel flag in Redis: %s", e)

    # 2. Update status in Database
    job.status = "failed"
    job.error_msg = "Job cancelled by user"
    job.updated_at = datetime.utcnow()
    db.commit()

    # 3. Publish cancellation message to WebSockets
    try:
        publish_progress(job_id, "error", message="Job cancelled by user", recoverable=True)
    except Exception as e:
        logger.error("Failed to publish cancellation event: %s", e)

    # 4. Celery revoke (just in case the task is still queued and hasn't started yet)
    try:
        from workers.celery_app import celery_app
        celery_app.control.revoke(job_id, terminate=True)
    except Exception as e:
        logger.warning("Celery revoke failed (expected if solo worker is busy): %s", e)

    # 5. Clean up temporary directories
    try:
        work_dir = settings.TEMP_DIR / job_id
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
            logger.info("Cancelled job %s: Cleaned up work directory %s", job_id, work_dir)

        dataset_dir = settings.OUTPUT_DIR / f"dataset_{job_id}"
        if dataset_dir.exists():
            shutil.rmtree(dataset_dir, ignore_errors=True)
            logger.info("Cancelled job %s: Cleaned up dataset directory %s", job_id, dataset_dir)
    except Exception as e:
        logger.error("Failed to clean up directories on cancel for job %s: %s", job_id, e)

    return {"status": "cancelled", "job_id": job_id}
