"""Prepare source audio for playback and save manual edits against its timeline."""

import hashlib
import logging
import os
import tempfile
from collections.abc import Iterable, Sequence
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
    return create_edits(db, source, [(start, end)], method, replace=replace)[0]


def create_edits(
    db: Session, source: RvcSource, ranges: list[tuple[float, float]], method: str,
    *, clip: RvcClip | None = None, replace: list[RvcClip] | None = None,
) -> list[dict]:
    offset = source_offset(source)
    minimum = offset if clip is None else 0.0
    frame_offset = 0 if clip is None else round((clip.start - offset) * WORK_RATE)
    prefix = "manual" if clip is None else "trim"

    for start, end in ranges:
        if start < minimum - (0.000001 if clip is None else 0) or end <= start or end - start > MAX_EDIT_SECONDS:
            raise MediaError(f"请选择音轨内起点早于终点、时长不超过 {MAX_EDIT_SECONDS} 秒的范围。")
    path = prepare_source_audio(source) if clip is None else clip_path(clip)

    def read_ranges():
        for start, end in ranges:
            first = max(0, round((start - minimum) * WORK_RATE))
            last = round((end - minimum) * WORK_RATE)
            raw, frames = read_edit_range(path, first, last)
            if clip is not None and first == 0 and last == frames:
                raise MediaError("请缩小截取范围，以生成新的片段。")
            yield raw, first, f"{prefix}:{source.id}:{frame_offset + first}:{frame_offset + last}:"

    return save_edits(
        db, source, read_ranges(), offset=offset if clip is None else clip.start, method=method,
        normalize=clip is None and source.options.get("normalize", False), replace=replace,
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


def save_edits(
    db: Session, source: RvcSource, edits: Iterable[tuple[bytes, int, str]], *,
    offset: float, method: str, normalize: bool, replace: list[RvcClip] | None = None,
) -> list[dict]:
    project_id = source.project_id
    project_clips = select(RvcClip).where(RvcClip.project_id == project_id)
    destinations = []
    digests = set()
    clips = []

    try:
        for raw, first, digest_key in edits:
            digest = hashlib.sha256(digest_key.encode() + raw).hexdigest()
            if digest in digests:
                raise MediaError("有重复的选区，请调整或移除后再保存。")
            digests.add(digest)
            clip = db.scalar(project_clips.where(RvcClip.digest == digest))
            if clip is None:
                generation = new_id()
                root = work_dir(project_id, source.id)
                directory = root / generation
                check_space(root, len(raw))
                directory.mkdir()
                destination = directory / "000001.wav"
                destinations.append(destination)
                data = write_clip(raw, destination, offset, first, normalize, manual=True)
                data["digest"] = digest
                data["quality"]["edit"] = method
                clip = RvcClip(id=new_id(), project_id=project_id, source_id=source.id, generation=generation, **data)
                db.add(clip)
            elif (buffered := db.get(RvcMergeBuffer, clip.id)) is not None:
                restore_buffer_audio(clip)
                db.delete(buffered)
            clips.append(clip)

        retired = [] if replace is None else [item for item in replace if item.id != clips[0].id]
        db.add_all(RvcMergeBuffer(clip_id=item.id) for item in retired)
        db.commit()
    except Exception as exc:
        db.rollback()
        for destination in destinations:
            destination.unlink(missing_ok=True)
            destination.parent.rmdir()
        if isinstance(exc, IntegrityError):
            if replace is None and len(digests) == 1 and (clip := db.scalar(
                project_clips.where(RvcClip.digest == next(iter(digests)), RvcClip.id.not_in(select(RvcMergeBuffer.clip_id)))
            )) is not None:
                return [clip_data(clip, source)]
            raise MediaError("片段已被另一项操作修改，请重新选择。") from exc
        raise

    results = [clip_data(clip, source) for clip in clips]
    if replace is not None:
        results[0]["replaced_count"] = len(retired)
    return results


def restore_buffer_audio(clip: RvcClip) -> None:
    path = clip_path(clip)
    staged = path.with_suffix(".deleting")
    if not path.exists() and staged.is_file():
        os.replace(staged, path)


def delete_buffered_clips(db: Session, rows: Sequence[tuple[RvcClip, RvcMergeBuffer]]) -> None:
    if not rows:
        return
    paths = [clip_path(clip) for clip, _ in rows]
    moved = []
    try:
        for path in paths:
            if path.is_file():
                staged = path.with_suffix(".deleting")
                try:
                    os.replace(path, staged)
                except OSError as exc:
                    raise MediaError("暂时无法移除音频文件，请暂停试听后重试。") from exc
                moved.append((path, staged))
        for _, entry in rows:
            db.delete(entry)
        db.flush()
        for clip, _ in rows:
            db.delete(clip)
        db.commit()
    except Exception:
        db.rollback()
        for path, staged in reversed(moved):
            try:
                os.replace(staged, path)
            except OSError:
                logger.warning("Could not restore staged audio file %s", staged, exc_info=True)
        raise

    for path in paths:
        staged = path.with_suffix(".deleting")
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not clean up staged audio file %s", staged, exc_info=True)
    for directory in {path.parent for path in paths}:
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            logger.warning("Could not clean up audio directory %s", directory, exc_info=True)
