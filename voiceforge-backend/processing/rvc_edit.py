"""Prepare source audio for playback and save manual edits against its timeline."""

import hashlib
import os
import tempfile
from pathlib import Path
from threading import Lock

import soundfile as sf
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from processing.rvc_audio import MediaError, WORK_RATE, check_space, ffmpeg, media_path, write_clip
from rvc_projects import RvcClip, RvcSource, clip_data, new_id, work_dir
from sources.rvc_online import download

_cache_lock = Lock()
MAX_EDIT_SECONDS = 120


def source_audio_path(project_id: str, source_id: str) -> Path:
    return work_dir(project_id, source_id) / "source-audio.flac"


def cache_decoded_audio(audio: Path, target: Path, check) -> None:
    ffmpeg(
        ["-f", "s16le", "-ar", str(WORK_RATE), "-ac", "1", "-i", media_path(audio),
         "-c:a", "flac", "-compression_level", "1", "-threads", "1", media_path(target)],
        check,
    )


def source_offset(source: RvcSource) -> float:
    return next(
        (track["offset"] for track in source.media.get("tracks", []) if track["index"] == source.track_index),
        0.0,
    )


def prepare_source_audio(source: RvcSource) -> Path:
    target = source_audio_path(source.project_id, source.id)
    if target.is_file():
        return target
    # Projects processed before audio caching recover their selected track on demand.
    with _cache_lock:
        if target.is_file():
            return target
        root = target.parent
        root.mkdir(parents=True, exist_ok=True)
        check_space(root, int((source.media.get("duration") or 0) * WORK_RATE * 2))
        with tempfile.TemporaryDirectory(prefix="edit-audio-", dir=root) as temp:
            scratch = Path(temp)
            if source.kind in {"bilibili", "youtube"}:
                original = download(source.origin, scratch, lambda: check_space(root), lambda _: None)
            else:
                original = Path(source.origin)
                if not original.is_file():
                    raise MediaError("原始素材已移动或无法读取，无法载入原音轨。")
            prepared = scratch / target.name
            ffmpeg(
                ["-protocol_whitelist", "file", "-threads", "1", "-i", media_path(original),
                 "-map", f"0:{source.track_index if source.track_index is not None else 'a:0'}", "-vn",
                 "-ac", "1", "-ar", str(WORK_RATE), "-c:a", "flac", "-sample_fmt", "s16",
                 "-compression_level", "1", "-threads", "1", media_path(prepared)],
                lambda: check_space(root),
            )
            if not sf.info(str(prepared)).frames:
                raise MediaError("原音轨没有可播放的音频。")
            os.replace(prepared, target)
    return target


def source_audio_info(source: RvcSource) -> dict:
    path = prepare_source_audio(source)
    info = sf.info(str(path))
    return {"duration": info.frames / info.samplerate, "offset": source_offset(source)}


def create_edit(db: Session, source: RvcSource, start: float, end: float, method: str) -> dict:
    offset = source_offset(source)
    if start < offset - 0.000001 or end <= start or end - start > MAX_EDIT_SECONDS:
        raise MediaError(f"请选择原音轨内起点早于终点、时长不超过 {MAX_EDIT_SECONDS} 秒的范围。")

    path = prepare_source_audio(source)
    with sf.SoundFile(str(path)) as audio:
        first = max(0, round((start - offset) * WORK_RATE))
        last = round((end - offset) * WORK_RATE)
        if not first < last <= audio.frames:
            raise MediaError("所选范围超出了原音轨。")
        audio.seek(first)
        raw = audio.read(last - first, dtype="int16").astype("<i2", copy=False).tobytes()

    project_id = source.project_id
    digest = hashlib.sha256(f"manual:{source.id}:{first}:{last}:".encode() + raw).hexdigest()
    existing = select(RvcClip).where(RvcClip.project_id == project_id, RvcClip.digest == digest)
    if clip := db.scalar(existing):
        return clip_data(clip, source)

    generation = new_id()
    directory = path.parent / generation
    check_space(path.parent, len(raw))
    directory.mkdir()
    destination = directory / "000001.wav"
    try:
        data = write_clip(raw, destination, offset, first, source.options.get("normalize", False), manual=True)
        data["digest"] = digest
        data["quality"]["edit"] = method
        clip = RvcClip(project_id=project_id, source_id=source.id, generation=generation, **data)
        db.add(clip)
        db.commit()
    except IntegrityError:
        db.rollback()
        destination.unlink(missing_ok=True)
        directory.rmdir()
        if clip := db.scalar(existing):
            return clip_data(clip, source)
        raise
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return clip_data(clip, source)
