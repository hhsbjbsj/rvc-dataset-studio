"""Search preview endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from schemas import SearchPreviewRequest, SearchPreviewResponse, SourceFound
from resolvers.character_resolver import resolve_character
from sources.huggingface import search_huggingface

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("/preview", response_model=SearchPreviewResponse)
async def search_preview(request: SearchPreviewRequest):
    try:
        character_info = await resolve_character(
            request.query, request.query_type, request.language
        )
    except Exception as e:
        logger.warning("Character resolution failed: %s", e)
        character_info = {"name": request.query, "language": request.language}

    sources: list[SourceFound] = []

    try:
        hf_results = await search_huggingface(request.query, character_info)
        for r in hf_results[:3]:
            sources.append(
                SourceFound(
                    type="huggingface",
                    dataset=r.get("id"),
                    size_gb=r.get("size_gb"),
                    clips=r.get("clips"),
                )
            )
    except Exception as e:
        logger.warning("HuggingFace search failed: %s", e)

    try:
        from sources.youtube import search_youtube

        yt_results = await search_youtube(request.query, request.query_type, character_info)
        if yt_results:
            total_duration = sum(v.get("duration", 0) for v in yt_results) / 60
            sources.append(
                SourceFound(
                    type="youtube",
                    videos=len(yt_results),
                    estimated_duration_minutes=round(total_duration, 1),
                )
            )
    except Exception as e:
        logger.warning("YouTube search failed: %s", e)

    if request.query_type == "anime" and request.language in ("jp", "auto", "both"):
        try:
            from sources.bilibili import search_bilibili

            bili_results = await search_bilibili(request.query, character_info)
            if bili_results:
                total_duration = sum(v.get("duration", 0) for v in bili_results) / 60
                sources.append(
                    SourceFound(
                        type="bilibili",
                        videos=len(bili_results),
                        estimated_duration_minutes=round(total_duration, 1),
                    )
                )
        except Exception as e:
            logger.warning("Bilibili search failed: %s", e)

    has_hub = any(s.type == "huggingface" for s in sources)
    has_video = any(s.type in ("youtube", "bilibili") for s in sources)

    if has_hub and has_video:
        strategy = "hybrid"
    elif has_hub:
        strategy = "dataset_hub"
    elif has_video:
        strategy = "video_scrape"
    else:
        strategy = "video_scrape"

    est_clips = 0
    est_duration = 0.0
    for s in sources:
        if s.clips:
            est_clips += s.clips
        if s.estimated_duration_minutes:
            est_duration += s.estimated_duration_minutes
        if s.videos:
            est_clips += s.videos * 50

    if est_clips == 0:
        est_clips = 500
        est_duration = 30.0

    return SearchPreviewResponse(
        sources_found=sources,
        recommended_strategy=strategy,
        estimated_output_clips=min(est_clips, 5000),
        estimated_duration_minutes=round(est_duration * 0.7, 1),
        character_info=character_info,
    )
