"""Offline extraction and bounded-memory segmentation for RVC projects."""

import hashlib
import json
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import webrtcvad

from processing.audio_utils import estimate_snr, rms_db

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".ts", ".m2ts", ".mpeg", ".mpg", ".m4v", ".wmv", ".3gp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aif", ".aiff", ".mka", ".ac3", ".webm", ".m4b"}
WORK_RATE = 48000
VAD_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = VAD_RATE * FRAME_MS // 1000
RESERVE_BYTES = 1024 ** 3


class MediaError(RuntimeError):
    pass


class ProcessingCancelled(RuntimeError):
    pass


def media_path(path: Path) -> str:
    value = str(path)
    if os.name == "nt" and len(value) >= 248 and not value.startswith(chr(92) * 2 + "?" + chr(92)):
        prefix = chr(92) * 2 + "?" + chr(92)
        if value.startswith(chr(92) * 2):
            return prefix + "UNC" + chr(92) + value[2:]
        return prefix + value
    return value


def number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def probe(path: Path) -> dict:
    command = [
        "ffprobe", "-v", "error", "-threads", "1", "-protocol_whitelist", "file",
        "-show_format", "-show_streams", "-of", "json", "-i", media_path(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=30, check=False)
    except FileNotFoundError as exc:
        raise MediaError("找不到 FFprobe，请安装 FFmpeg 并加入 PATH。") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("FFprobe 读取超时。") from exc
    if result.returncode:
        detail = result.stderr.strip()[:500] or f"FFprobe 退出代码 {result.returncode}"
        raise MediaError(f"无法读取媒体：{detail}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    info = data.get("format", {})
    start = number(info.get("start_time")) or 0
    tracks = [
        {
            "index": track["index"],
            "codec": track.get("codec_name"),
            "sample_rate": int(track.get("sample_rate") or 0),
            "channels": track.get("channels"),
            "language": track.get("tags", {}).get("language"),
            "title": track.get("tags", {}).get("title"),
            "default": bool(track.get("disposition", {}).get("default")),
            "offset": max(0, track_start - start) if (track_start := number(track.get("start_time"))) is not None else 0,
            "duration": number(track.get("duration")),
        }
        for track in streams if track["codec_type"] == "audio"
    ]
    if not tracks:
        raise MediaError("文件没有可用的音轨。")
    return {
        "duration": number(info.get("duration")) or max((track["duration"] or 0 for track in tracks), default=0),
        "has_video": any(
            stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")
            for stream in streams
        ),
        "tracks": tracks,
        "default_track": next((track["index"] for track in tracks if track["default"]), tracks[0]["index"]),
    }


def check_space(path: Path, required: int = 0) -> None:
    if shutil.disk_usage(path).free < required + RESERVE_BYTES:
        raise MediaError("工作盘可用空间不足，已保留 1 GiB 安全余量。")


def ffmpeg(args: list[str], check=lambda: None) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-filter_threads", "1", *args]
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise MediaError("找不到 FFmpeg，请安装并加入 PATH。") from exc
    try:
        while True:
            check()
            try:
                _, errors = process.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode:
        detail = errors.decode("utf-8", errors="replace").strip()[:500] or f"退出代码 {process.returncode}"
        raise MediaError(f"FFmpeg 处理失败：{detail}")


def extract_pcm(source: Path, stream_index: int, audio: Path, vad: Path | None, check) -> None:
    args = ["-protocol_whitelist", "file", "-threads", "1", "-i", media_path(source)]
    outputs = [(audio, WORK_RATE)]
    if vad is not None:
        outputs.append((vad, VAD_RATE))
    for path, rate in outputs:
        args.extend(["-map", f"0:{stream_index}", "-vn", "-ac", "1", "-ar", str(rate),
                     "-c:a", "pcm_s16le", "-f", "s16le", media_path(path)])
    ffmpeg(args, check)


def speech_regions(path: Path, check) -> list[tuple[int, int]]:
    """Read 30 ms WebRTC frames, smoothing onset/offset and retaining a short margin."""
    vad = webrtcvad.Vad(2)
    regions = []
    active = False
    onset = quiet = position = 0
    frame_bytes = FRAME_SAMPLES * 2
    margin = FRAME_SAMPLES * 3
    silence_frames = 10
    with path.open("rb") as pcm:
        while block := pcm.read(frame_bytes * 200):
            check()
            for index in range(0, len(block), frame_bytes):
                frame = block[index:index + frame_bytes]
                if len(frame) < frame_bytes:
                    break
                voiced = vad.is_speech(frame, VAD_RATE)
                if active:
                    quiet = 0 if voiced else quiet + 1
                    if quiet >= silence_frames:
                        end = position + FRAME_SAMPLES - quiet * FRAME_SAMPLES + margin
                        regions.append((start, end))
                        active = False
                        onset = 0
                else:
                    onset = onset + 1 if voiced else 0
                    if onset == 2:
                        start = max(0, position - FRAME_SAMPLES - margin)
                        if regions:
                            start = max(start, regions[-1][1])
                        quiet = 0
                        active = True
                position += FRAME_SAMPLES
    if active:
        end = position - quiet * FRAME_SAMPLES + margin
        regions.append((start, min(position, end)))
    return [(start * WORK_RATE // VAD_RATE, end * WORK_RATE // VAD_RATE) for start, end in regions]


def quiet_cut(pcm, low: int, high: int, preferred: int) -> int:
    start = max(low, preferred - WORK_RATE // 2)
    end = min(high, preferred + WORK_RATE // 2)
    step = WORK_RATE // 50
    if end - start < step:
        return preferred
    pcm.seek(start * 2)
    samples = np.frombuffer(pcm.read((end - start) * 2), dtype="<i2").astype(np.float32)
    windows = samples[:len(samples) // step * step].reshape(-1, step)
    if not len(windows):
        return preferred
    energy = np.mean(windows ** 2, axis=1)
    return start + int(np.argmin(energy)) * step + step // 2


def clip_bounds(pcm, regions: list[tuple[int, int]], minimum: int, maximum: int):
    grouped: list[tuple[int, int]] = []
    for start, end in regions:
        if grouped:
            previous_start, previous_end = grouped[-1]
            can_merge = start - previous_end <= WORK_RATE * 0.75 and end - previous_start <= maximum
            is_short = previous_end - previous_start < minimum or end - start < minimum
            if can_merge and is_short:
                grouped[-1] = previous_start, end
                continue
        grouped.append((start, end))

    for start, end in grouped:
        length = end - start
        if length < minimum:
            continue
        parts = math.ceil(length / maximum)
        effective_min = min(minimum, length // parts)
        for remaining in range(parts, 1, -1):
            low = max(start + effective_min, end - (remaining - 1) * maximum)
            high = min(start + maximum, end - (remaining - 1) * effective_min)
            preferred = max(low, min(high, start + (end - start) // remaining))
            cut = quiet_cut(pcm, low, high, preferred)
            yield start, cut
            start = cut
        yield start, end


def make_clips(audio: Path, vad: Path | None, directory: Path, options: dict, offset: float, check) -> list[dict]:
    total = audio.stat().st_size // 2
    if not total:
        raise MediaError("音轨没有可解码的音频。")
    if vad is None and total > WORK_RATE * 120:
        raise MediaError("跳过切分时，单文件最长为 120 秒。请为长素材开启切分。")
    regions = speech_regions(vad, check) if vad is not None else None
    clips = []
    with audio.open("rb") as pcm:
        bounds = [(0, total)] if regions is None else clip_bounds(
            pcm, regions, int(options["min_seconds"] * WORK_RATE), int(options["max_seconds"] * WORK_RATE),
        )
        for start, end in bounds:
            check()
            end = min(end, total)
            if end <= start:
                continue
            pcm.seek(start * 2)
            raw = pcm.read((end - start) * 2)
            filename = f"{len(clips) + 1:06d}.wav"
            clip = write_clip(raw, directory / filename, offset, start, options["normalize"])
            if clip is not None:
                clips.append(clip)
    if not clips:
        raise MediaError("没有可用片段。请检查是否有语音，或调整最短片段时长。")
    return clips


def write_clip(raw: bytes, path: Path, offset: float, start: int, normalize: bool, *, manual: bool = False) -> dict | None:
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    peak = float(np.max(np.abs(samples)))
    if not manual and (peak < 0.003 or np.count_nonzero(np.abs(samples) >= 0.003) < WORK_RATE * 0.05):
        return None
    loudness = rms_db(samples)
    clipping = float(np.mean(np.abs(samples) >= 0.999))
    quiet = float(np.mean(np.abs(samples) < 0.01))
    snr = estimate_snr(samples, WORK_RATE)
    score = round(max(0, min(100, 50 + snr - 400 * clipping - 40 * quiet)), 1)
    if normalize and peak:
        gain = min(2.0, 0.9 / peak)
        raw_output = (samples * gain * 32768).astype("<i2").tobytes()
    else:
        raw_output = raw
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, WORK_RATE, 0, "NONE", "not compressed"))
        wav.writeframes(raw_output)
    return {
        "filename": path.name,
        "digest": hashlib.sha256(raw).hexdigest(),
        "start": round(offset + start / WORK_RATE, 6),
        "end": round(offset + (start + len(samples)) / WORK_RATE, 6),
        "duration": round(len(samples) / WORK_RATE, 6),
        "quality": {
            "score": score, "snr_db": round(snr, 2), "rms_db": round(loudness, 2),
            "clip_ratio": round(clipping, 6), "quiet_ratio": round(quiet, 6),
        },
    }
