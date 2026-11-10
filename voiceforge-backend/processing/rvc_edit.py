"""Prepare source audio for playback and save manual edits against its timeline."""

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from threading import Lock

import numpy as np
import soundfile as sf
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from processing.rvc_audio import MediaError, WORK_RATE, check_space, ffmpeg, media_path, write_clip
from rvc_projects import RvcClip, RvcMergeBuffer, RvcSource, clip_data, clip_path, new_id, work_dir
from sources.rvc_online import download

logger = logging.getLogger(__name__)
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


def waveform_peaks(path: Path) -> list[float]:
    """Summarize the audio in bounded blocks for drawing the waveform."""
    try:
        with sf.SoundFile(str(path)) as audio:
            if not audio.frames:
                raise MediaError("当前音频没有可显示的波形。")
            # Evenly space the bins across the actual audio duration.
            count = min(audio.frames, 8192)
            peaks = []
            previous = 0
            for index in range(1, count + 1):
                boundary = index * audio.frames // count
                samples = audio.read(boundary - previous, dtype="float32")
                peaks.append(round(float(np.max(np.abs(samples))), 5))
                previous = boundary
            return peaks
    except sf.LibsndfileError as exc:
        raise MediaError("当前音频文件无法读取。") from exc


def create_edit(
    db: Session, source: RvcSource, start: float, end: float, method: str,
    *, replace: list[RvcClip] | None = None,
) -> dict:
    offset = source_offset(source)
    if start < offset - 0.000001 or end <= start or end - start > MAX_EDIT_SECONDS:
        raise MediaError(f"请选择原音轨内起点早于终点、时长不超过 {MAX_EDIT_SECONDS} 秒的范围。")

    path = prepare_source_audio(source)
    first = max(0, round((start - offset) * WORK_RATE))
    last = round((end - offset) * WORK_RATE)
    raw, _ = read_edit_range(path, first, last)
    return save_edit(
        db, source, raw, offset=offset, first=first,
        digest_key=f"manual:{source.id}:{first}:{last}:", method=method,
        normalize=source.options.get("normalize", False), replace=replace,
    )


def trim_clip(db: Session, source: RvcSource, clip: RvcClip, start: float, end: float) -> dict:
    if start < 0 or end <= start or end - start > MAX_EDIT_SECONDS:
        raise MediaError(f"请选择起点早于终点、时长不超过 {MAX_EDIT_SECONDS} 秒的范围。")

    first, last = round(start * WORK_RATE), round(end * WORK_RATE)
    path = clip_path(clip)
    raw, frames = read_edit_range(path, first, last)
    if first == 0 and last == frames:
        raise MediaError("请缩小截取范围，以生成新的片段。")
    source_frame = round((clip.start - source_offset(source)) * WORK_RATE)
    return save_edit(
        db, source, raw, offset=clip.start, first=first,
        digest_key=f"trim:{source.id}:{source_frame + first}:{source_frame + last}:",
        method="trim", normalize=False,
    )


def read_edit_range(path: Path, first: int, last: int) -> tuple[bytes, int]:
    try:
        with sf.SoundFile(str(path)) as audio:
            if audio.samplerate != WORK_RATE or audio.channels != 1:
                raise MediaError("音频格式与工作台片段格式不匹配。")
            if not 0 <= first < last <= audio.frames:
                raise MediaError("所选范围超出了当前音频。")
            audio.seek(first)
            samples = audio.read(last - first, dtype="int16")
            if len(samples) != last - first:
                raise MediaError("当前音频文件不完整。")
            return samples.astype("<i2", copy=False).tobytes(), audio.frames
    except sf.LibsndfileError as exc:
        raise MediaError("当前音频文件无法读取。") from exc


def save_edit(
    db: Session, source: RvcSource, raw: bytes, *, offset: float, first: int,
    digest_key: str, method: str, normalize: bool, replace: list[RvcClip] | None = None,
) -> dict:
    project_id = source.project_id
    digest = hashlib.sha256(digest_key.encode() + raw).hexdigest()
    existing = select(RvcClip).where(RvcClip.project_id == project_id, RvcClip.digest == digest)
    clip = db.scalar(existing)
    buffered_result = db.get(RvcMergeBuffer, clip.id) if clip is not None else None
    if clip is not None and replace is None and buffered_result is None:
        return clip_data(clip, source)

    destination = None
    buffered_count = 0
    try:
        if clip is None:
            generation = new_id()
            root = work_dir(project_id, source.id)
            directory = root / generation
            check_space(root, len(raw))
            directory.mkdir()
            destination = directory / "000001.wav"
            data = write_clip(raw, destination, offset, first, normalize, manual=True)
            data["digest"] = digest
            data["quality"]["edit"] = method
            clip = RvcClip(id=new_id(), project_id=project_id, source_id=source.id, generation=generation, **data)
            db.add(clip)

        if buffered_result is not None:
            restore_buffer_audio(clip)
            db.delete(buffered_result)
        if replace is not None:
            retired = [item for item in replace if item.id != clip.id]
            db.add_all(RvcMergeBuffer(clip_id=item.id) for item in retired)
            buffered_count = len(retired)
        db.commit()
    except Exception as exc:
        db.rollback()
        if destination is not None:
            destination.unlink(missing_ok=True)
            destination.parent.rmdir()
        if isinstance(exc, IntegrityError):
            if replace is None and (clip := db.scalar(existing)) is not None:
                return clip_data(clip, source)
            raise MediaError("片段已被另一项操作修改，请重新选择。") from exc
        raise

    result = clip_data(clip, source)
    if replace is not None:
        result["replaced_count"] = buffered_count
    return result


def restore_buffer_audio(clip: RvcClip) -> None:
    path = clip_path(clip)
    staged = path.with_suffix(".deleting")
    if not path.exists() and staged.is_file():
        os.replace(staged, path)


def delete_buffered_clip(db: Session, clip: RvcClip, entry: RvcMergeBuffer) -> None:
    path = clip_path(clip)
    staged = path.with_suffix(".deleting")
    try:
        if path.is_file():
            os.replace(path, staged)
    except OSError as exc:
        raise MediaError("暂时无法移除音频文件，请暂停试听后重试。") from exc

    try:
        db.delete(entry)
        db.flush()
        db.delete(clip)
        db.commit()
    except Exception:
        db.rollback()
        restore_buffer_audio(clip)
        raise

    try:
        staged.unlink(missing_ok=True)
        if path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    except OSError:
        logger.warning("Could not clean up staged audio file %s", staged, exc_info=True)
