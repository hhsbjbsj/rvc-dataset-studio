"""Main character → metadata resolver."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def resolve_character(
    query: str,
    query_type: str,
    language: str = "auto",
) -> dict[str, Any]:
    """Resolve character metadata from multiple sources."""
    result = {
        "name": query.strip(),
        "aliases": [],
        "series": "",
        "language": language,
        "voice_actor_jp": "",
        "voice_actor_en": "",
        "query_type": query_type,
    }

    if query_type in ("anime", "fictional"):
        # Try MAL/Jikan first
        try:
            from resolvers.myanimelist import search_character

            mal_info = await search_character(query)
            if mal_info:
                result.update(mal_info)
        except Exception as e:
            logger.warning("MAL resolver failed: %s", e)

        # Try BTVA for English VA
        if language in ("en", "auto", "both"):
            try:
                from resolvers.btva import search_character_va

                btva_info = await search_character_va(query)
                if btva_info:
                    if btva_info.get("voice_actor_en"):
                        result["voice_actor_en"] = btva_info["voice_actor_en"]
                    if btva_info.get("series") and not result["series"]:
                        result["series"] = btva_info["series"]
            except Exception as e:
                logger.warning("BTVA resolver failed: %s", e)

        # AniDB for JP VA
        if language in ("jp", "auto", "both"):
            try:
                from resolvers.anidb import search_character_anidb

                anidb_info = await search_character_anidb(query)
                if anidb_info:
                    if anidb_info.get("voice_actor_jp"):
                        result["voice_actor_jp"] = anidb_info["voice_actor_jp"]
                    if anidb_info.get("series") and not result["series"]:
                        result["series"] = anidb_info["series"]
            except Exception as e:
                logger.warning("AniDB resolver failed: %s", e)

    elif query_type == "game_character":
        try:
            from resolvers.vgmdb import search_game_character

            vgm_info = await search_game_character(query)
            if vgm_info:
                result.update(vgm_info)
        except Exception as e:
            logger.warning("VGMdb resolver failed: %s", e)

    elif query_type in ("celebrity", "voice_actor", "musician", "youtuber"):
        try:
            from resolvers.wikipedia import search_person

            wiki_info = await search_person(query)
            if wiki_info:
                result.update(wiki_info)
        except Exception as e:
            logger.warning("Wikipedia resolver failed: %s", e)

    return result
