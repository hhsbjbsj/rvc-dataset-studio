"""Online source discovery and single-video download for mixed RVC projects."""

import html
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from processing.rvc_audio import MediaError, ProcessingCancelled

BILI_VIDEO = re.compile(r"/video/(BV[A-Za-z0-9]{10}|av[0-9]+)(?:/)?")
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
BILI_SHORT_PATH = re.compile(r"/[A-Za-z0-9_-]+/?")


def normalize_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or parts.username or parts.password or parts.port:
        raise ValueError("请填写完整的 B 站或 YouTube 视频链接。")
    host = parts.hostname
    query = parse_qs(parts.query)
    if host in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
        match = BILI_VIDEO.fullmatch(parts.path)
        if match:
            page = query.get("p", ["1"])[0]
            if not re.fullmatch(r"[1-9][0-9]{0,4}", page):
                raise ValueError("B 站分 P 参数无效。")
            suffix = "" if page == "1" else f"?p={page}"
            return "bilibili", f"https://www.bilibili.com/video/{match[1]}{suffix}"
    if host in {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}:
        if host != "youtu.be" and parts.path == "/watch":
            video = query.get("v", [""])[0]
        else:
            video = parts.path.removeprefix("/shorts/").strip("/")
        if YOUTUBE_ID.fullmatch(video):
            return "youtube", f"https://www.youtube.com/watch?v={video}"
    raise ValueError("支持 B 站 BV/av 视频链接和 YouTube 单视频链接。")


def resolve_source_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url.strip())
    if parts.hostname != "b23.tv":
        return normalize_url(url)

    if (
        parts.scheme not in {"http", "https"}
        or parts.username is not None or parts.password is not None or parts.port is not None
        or not BILI_SHORT_PATH.fullmatch(parts.path)
    ):
        raise ValueError("B 站短链接格式无效。")

    try:
        response = httpx.get(f"https://b23.tv{parts.path}", follow_redirects=False, timeout=8)
    except httpx.HTTPError as exc:
        raise ValueError("B 站短链接解析失败，请重试或粘贴 BV/av 视频链接。") from exc

    # Accept a direct Bilibili video redirect without following arbitrary targets.
    if not response.has_redirect_location:
        raise ValueError("B 站短链接未返回有效的视频地址。")
    try:
        platform, canonical = normalize_url(response.headers["location"])
    except ValueError as exc:
        raise ValueError("该 B 站短链接未指向 BV/av 视频。") from exc
    if platform != "bilibili":
        raise ValueError("该 B 站短链接未指向 BV/av 视频。")
    return platform, canonical


def duration_seconds(value: str) -> int:
    total = 0
    for part in str(value).split(":"):
        if not part.isdigit():
            return 0
        total = total * 60 + int(part)
    return total


def search_bilibili_page(query: str, page: int) -> dict:
    try:
        response = httpx.get(
            "https://api.bilibili.com/x/web-interface/search/type",
            params={"search_type": "video", "keyword": query, "page": page},
            headers={"Referer": "https://search.bilibili.com/", "User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise MediaError(f"B 站搜索暂不可用（返回码 {payload.get('code')}）。可以手动添加视频链接。")
        data = payload.get("data") or {}
    except (httpx.HTTPError, ValueError) as exc:
        raise MediaError("B 站搜索请求失败。可以手动添加视频链接。") from exc

    videos = {}
    for entry in data.get("result") or []:
        if not isinstance(entry, dict) or not entry.get("bvid"):
            continue
        try:
            platform, url = normalize_url(f"https://www.bilibili.com/video/{entry['bvid']}")
        except ValueError:
            continue
        videos[url] = {
            "url": url, "platform": platform,
            "title": html.unescape(re.sub(r"<[^>]*>", "", entry.get("title") or "")),
            "uploader": entry.get("author") or "",
            "duration": duration_seconds(entry.get("duration", "")),
        }
    total_pages = duration_seconds(data.get("numPages") or 0)
    return {"videos": list(videos.values()), "has_more": page < total_pages}


def download(url: str, directory: Path, check, update_progress) -> Path:
    import yt_dlp

    platform, url = normalize_url(url)

    def hook(data):
        check()
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        if total and data.get("status") == "downloading":
            update_progress(min(39, int(39 * data.get("downloaded_bytes", 0) / total)))

    def single_entry(info, *, incomplete):
        if info.get("_type") in {"playlist", "multi_video"}:
            return "Select a single video or a specific part."
        return None

    options = {
        "quiet": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(directory / "download.%(ext)s"),
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
        "match_filter": single_entry,
        "progress_hooks": [hook],
        "allowed_extractors": [platform],
        "js_runtimes": {"node": {}},
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            if not info or info.get("entries") is not None:
                raise MediaError("该链接没有返回单个视频，请明确指定视频或分 P 链接。")
            path = Path(downloader.prepare_filename(info))
    except ProcessingCancelled:
        raise
    except yt_dlp.utils.DownloadError as exc:
        check()
        raise MediaError(f"下载失败：{str(exc)[:350]}") from exc
    if not path.is_file():
        raise MediaError("下载结束，但没有找到媒体文件。")
    return path
