"""API integration tests."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from database import init_db

init_db()
client = TestClient(app)


def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_api_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200


@patch("workers.pipeline.run_pipeline")
def test_create_job(mock_pipeline):
    mock_pipeline.apply_async.return_value = None
    resp = client.post("/api/jobs/create", json={
        "query": "Test Character",
        "query_type": "anime",
        "language": "jp",
        "options": {
            "target_duration_minutes": 10,
            "quality_threshold": 50,
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"
    mock_pipeline.apply_async.assert_called_once()


def test_get_job_not_found():
    resp = client.get("/api/jobs/nonexistent-id")
    assert resp.status_code == 404


def test_history():
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_delete_job_cleans_up_files():
    from database import get_db_session, Job
    from config import get_settings
    
    settings = get_settings()
    
    # 1. Create a dummy job in the DB
    db = get_db_session()
    job_id = "test-delete-job-id"
    old_job = db.query(Job).filter(Job.id == job_id).first()
    if old_job:
        db.delete(old_job)
        db.commit()
        
    # Create mock directories and files
    work_dir = settings.TEMP_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    test_temp_file = work_dir / "temp_audio.wav"
    test_temp_file.write_bytes(b"mock data")
    
    zip_path = settings.OUTPUT_DIR / f"voiceforge_{job_id}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(b"mock zip")
    
    job = Job(
        id=job_id,
        query="Test Delete",
        query_type="anime",
        status="done",
        output_path=str(zip_path)
    )
    db.add(job)
    db.commit()
    db.close()
    
    # 2. Call delete API
    resp = client.delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    
    # 3. Verify files and directories are deleted
    assert not work_dir.exists()
    assert not zip_path.exists()
    
    # 4. Verify job is gone from DB
    db = get_db_session()
    deleted_job = db.query(Job).filter(Job.id == job_id).first()
    assert deleted_job is None
    db.close()


def test_init_db_prunes_orphaned_files():
    from database import init_db, get_db_session, Job
    from config import get_settings
    import uuid
    
    settings = get_settings()
    
    # 1. Create a dummy active job in the DB so it is NOT pruned
    db = get_db_session()
    active_job_id = str(uuid.uuid4())
    job = Job(
        id=active_job_id,
        query="Active Job",
        query_type="anime",
        status="done"
    )
    db.add(job)
    db.commit()
    db.close()
    
    # Create active temp dir and zip file
    active_temp_dir = settings.TEMP_DIR / active_job_id
    active_temp_dir.mkdir(parents=True, exist_ok=True)
    active_zip = settings.OUTPUT_DIR / f"voiceforge_{active_job_id}.zip"
    active_zip.write_bytes(b"active zip")
    
    # 2. Create orphaned temp dir (UUID) and zip file
    orphaned_job_id = str(uuid.uuid4())
    orphaned_temp_dir = settings.TEMP_DIR / orphaned_job_id
    orphaned_temp_dir.mkdir(parents=True, exist_ok=True)
    orphaned_zip = settings.OUTPUT_DIR / f"voiceforge_{orphaned_job_id}.zip"
    orphaned_zip.write_bytes(b"orphaned zip")
    
    # Create non-UUID directory to verify it is NOT pruned (e.g. system folder)
    non_uuid_dir = settings.TEMP_DIR / "some_custom_config_dir"
    non_uuid_dir.mkdir(parents=True, exist_ok=True)
    
    # 3. Call init_db() to trigger pruning
    init_db()
    
    # 4. Assertions
    assert active_temp_dir.exists()
    assert active_zip.exists()
    assert not orphaned_temp_dir.exists()
    assert not orphaned_zip.exists()
    assert non_uuid_dir.exists()
    
    # Cleanup database
    db = get_db_session()
    db.delete(db.query(Job).filter(Job.id == active_job_id).first())
    db.commit()
    db.close()
    
    # Cleanup files
    import shutil
    shutil.rmtree(active_temp_dir, ignore_errors=True)
    active_zip.unlink(missing_ok=True)
    shutil.rmtree(non_uuid_dir, ignore_errors=True)


def test_convert_endpoint_database_tracking():
    # Generate a dummy WAV file
    import io
    import soundfile as sf
    import numpy as np
    from database import get_db_session, Job

    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    data = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    
    wav_bytes = io.BytesIO()
    sf.write(wav_bytes, data, sr, format='WAV', subtype='PCM_16')
    wav_bytes.seek(0)

    # Call endpoint
    resp = client.post(
        "/api/convert",
        files=[("files", ("test.wav", wav_bytes, "audio/wav"))],
        data={
            "target_format": "wav",
            "sample_rate": "22050",
            "bit_depth": "16",
            "channels": "mono",
            "normalize": "true",
            "trim_silence": "false",
        }
    )
    assert resp.status_code == 200
    res_data = resp.json()
    job_id = res_data["job_id"]
    
    # Assert DB record exists
    db = get_db_session()
    db_job = db.query(Job).filter(Job.id == job_id).first()
    assert db_job is not None
    assert db_job.query_type == "conversion"
    assert db_job.status == "done"
    assert db_job.total_clips == 1
    
    # Assert download works
    dl_resp = client.get(f"/api/jobs/{job_id}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"] == "application/zip"
    
    # Delete job and assert cleaned up
    del_resp = client.delete(f"/api/jobs/{job_id}")
    assert del_resp.status_code == 200
    assert db.query(Job).filter(Job.id == job_id).first() is None
    db.close()

