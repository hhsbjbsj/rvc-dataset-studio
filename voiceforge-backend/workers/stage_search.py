"""Dataset hub search stage."""

import logging
from typing import Any

from sources.huggingface import search_huggingface
from sources.youtube import search_youtube
from sources.bilibili import search_bilibili
from sources.openslr import search_openslr
from sources.nicovideo import search_nicovideo

logger = logging.getLogger(__name__)


async def search_all_sources(
    query: str,
    query_type: str,
    character_info: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Search all data sources and determine strategy."""
    strategy_pref = options.get("search_strategy", "auto")
    sources = []
    hf_results = []
    yt_results = []
    bili_results = []

    # HuggingFace
    if strategy_pref in ("auto", "hub_only", "hybrid"):
        hf_results = await search_huggingface(query, character_info)
        for r in hf_results[:3]:
            sources.append({"type": "huggingface", "dataset": r["id"], "clips": r.get("clips", 0)})

    # YouTube
    if strategy_pref in ("auto", "scrape_only", "hybrid"):
        if "youtube" in options.get("video_sources", ["youtube"]):
            yt_results = await search_youtube(query, query_type, character_info)
            for r in yt_results[:20]:
                sources.append({"type": "youtube", "url": r["url"], "title": r["title"], "duration": r["duration"]})

    # Bilibili
    if (
        strategy_pref in ("auto", "scrape_only", "hybrid")
        and query_type in ("anime", "fictional", "game_character")
        and "bilibili" in options.get("video_sources", [])
    ):
        bili_results = await search_bilibili(query, character_info)
        for r in bili_results[:15]:
            sources.append({"type": "bilibili", "url": r.get("url", ""), "title": r.get("title", "")})

    # Nicovideo
    if "nicovideo" in options.get("video_sources", []):
        nico_results = await search_nicovideo(query, character_info)
        for r in nico_results[:10]:
            sources.append({"type": "nicovideo", "url": r.get("url", ""), "title": r.get("title", "")})

    # OpenSLR
    openslr_results = await search_openslr(query)
    for r in openslr_results[:3]:
        sources.append({"type": "openslr", "id": r.get("id", ""), "title": r.get("title", "")})

    # Determine strategy
    has_hub = len(hf_results) > 0
    has_video = len(yt_results) > 0 or len(bili_results) > 0

    if strategy_pref == "hub_only":
        strategy = "dataset_hub"
    elif strategy_pref == "scrape_only":
        strategy = "video_scrape"
    elif has_hub and has_video:
        strategy = "hybrid"
    elif has_hub:
        strategy = "dataset_hub"
    else:
        strategy = "video_scrape"

    return {
        "strategy": strategy,
        "sources": sources,
        "hf_results": hf_results,
        "yt_results": yt_results,
        "bili_results": bili_results,
    }
