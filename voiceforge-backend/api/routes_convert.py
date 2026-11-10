"""Standalone file conversion endpoints."""

import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import get_settings
from processing.converter import convert_file
from database import Job, get_db

router = APIRouter(prefix="/api", tags=["convert"])
settings = get_settings()


def _safe_upload_name(filename: str | None, index: int, used: set[str]) -> str:
    """Strip any directory part from a client-supplied filename (no path traversal) and
    keep names unique so two uploads called e.g. take.wav do not overwrite each other."""
    name = Path((filename or "").replace("\\", "/")).name.strip()
    if not name or name in (".", ".."):
        name = f"upload_{index:03d}"
    stem, dot, ext = name.partition(".")
    candidate, n = name, 1
    while candidate.lower() in used:
        candidate = f"{stem}_{n}{dot}{ext}"
        n += 1
    used.add(candidate.lower())
    return candidate


# A plain (sync) endpoint: FastAPI runs it in a worker thread, so a long conversion no
# longer blocks the event loop (health checks, WebSockets and other requests keep working).
@router.post("/convert")
def convert_files(
    files: list[UploadFile] = File(...),
    target_format: str = Form("wav"),
    sample_rate: int = Form(22050),
    bit_depth: int = Form(16),
    channels: str = Form("mono"),
    normalize: bool = Form(True),
    trim_silence: bool = Form(False),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException

    # These values end up in file names and ffmpeg arguments, so only accept known ones.
    target_format = target_format.lower().lstrip(".")
    if target_format not in ("wav", "flac"):
        raise HTTPException(status_code=400, detail="target_format must be wav or flac")
    if channels not in ("mono", "stereo"):
        raise HTTPException(status_code=400, detail="channels must be mono or stereo")
    if bit_depth not in (16, 24, 32):
        raise HTTPException(status_code=400, detail="bit_depth must be 16, 24 or 32")
    if not 8000 <= sample_rate <= 192000:
        raise HTTPException(status_code=400, detail="sample_rate must be between 8000 and 192000")

    job_id = str(uuid.uuid4())
    work_dir = settings.TEMP_DIR / f"convert_{job_id}"
    output_dir = work_dir / "output"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    errors: list[str] = []

    used_names: set[str] = set()
    for index, upload in enumerate(files):
        input_path = work_dir / _safe_upload_name(upload.filename, index, used_names)
        with open(input_path, "wb") as f:
            shutil.copyfileobj(upload.file, f, length=1024 * 1024)

        try:
            results = convert_file(
                input_path,
                output_dir,
                target_format=target_format,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                channels=channels,
                normalize=normalize,
                trim_silence=trim_silence,
            )
            outputs.extend(results)
        except Exception as e:
            errors.append(f"{upload.filename}: {e}")

    # Package as ZIP
    zip_path = settings.OUTPUT_DIR / f"convert_{job_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for audio in outputs:
            zf.write(audio, audio.relative_to(output_dir))
    converted = len(outputs)

    # Save to database
    from datetime import datetime
    db_job = Job(
        id=job_id,
        query=f"Audio Conversion ({len(files)} files)",
        query_type="conversion",
        status="done" if converted > 0 else "failed",
        total_clips=converted,
        output_path=str(zip_path) if converted > 0 else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_job.set_options({
        "output_format": target_format,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "channels": channels,
        "normalize": normalize,
        "trim_silence": trim_silence
    })
    db.add(db_job)
    db.commit()

    return {
        "job_id": job_id,
        "status": db_job.status,
        "files_converted": converted,
        "errors": errors,
        "download_url": f"/api/convert/{job_id}/download",
    }


@router.get("/convert/{job_id}/download")
def download_converted(job_id: str):
    from fastapi import HTTPException

    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversion output not found")
    zip_path = settings.OUTPUT_DIR / f"convert_{job_id}.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Conversion output not found")
    return FileResponse(zip_path, media_type="application/zip", filename=f"converted_{job_id}.zip")
