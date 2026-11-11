"""Job history endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Job, RemovedHistoryEntry, get_db
from schemas import HistoryItem

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=list[HistoryItem])
def get_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    jobs = (
        db.query(Job)
        .filter(Job.id.not_in(select(RemovedHistoryEntry.item_id).where(RemovedHistoryEntry.kind == "job")))
        .order_by(Job.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [
        HistoryItem(
            id=j.id,
            query=j.query,
            query_type=j.query_type,
            language=j.language,
            status=j.status,
            total_clips=j.total_clips,
            total_seconds=j.total_seconds,
            created_at=j.created_at,
            download_url=f"/api/jobs/{j.id}/download" if j.output_path else None,
        )
        for j in jobs
    ]
