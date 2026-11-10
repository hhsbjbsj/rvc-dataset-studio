"""Voice Activity Detection wrapper (Silero + webrtcvad)."""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def get_speech_segments(
    audio_path: Path,
    sample_rate: int = 16000,
    min_speech_ms: int = 500,
    min_silence_ms: int = 300,
    threshold: float = 0.5,
) -> list[dict[str, float]]:
    """Detect speech segments using Silero VAD."""
    try:
        vad_rate = sample_rate if sample_rate in (8000, 16000) else 16000
        return _silero_vad(audio_path, vad_rate, min_speech_ms, min_silence_ms, threshold)
    except Exception as e:
        logger.warning("Silero VAD failed, falling back to webrtcvad: %s", e)
        return _webrtc_vad(audio_path, sample_rate)


def _silero_vad(
    audio_path: Path,
    sample_rate: int,
    min_speech_ms: int,
    min_silence_ms: int,
    threshold: float,
) -> list[dict[str, float]]:
    from models.silero_loader import get_silero_model

    model, utils = get_silero_model()
    get_speech_timestamps, _, read_audio, *_ = utils

    wav = read_audio(str(audio_path), sampling_rate=sample_rate)
    timestamps = get_speech_timestamps(
        wav, model,
        threshold=threshold,
        sampling_rate=sample_rate,
        min_speech_duration_ms=min_speech_ms,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=100,
    )

    return [
        {"start": t["start"] / sample_rate, "end": t["end"] / sample_rate}
        for t in timestamps
    ]


def _webrtc_vad(audio_path: Path, sample_rate: int) -> list[dict[str, float]]:
    import webrtcvad

    from processing.audio_utils import load_audio, resample

    data, sr = load_audio(audio_path)
    if sr != 16000:
        data = resample(data, sr, 16000)
        sr = 16000

    # Convert to 16-bit PCM
    pcm = (data * 32767).astype(np.int16)
    vad = webrtcvad.Vad(2)
    frame_duration_ms = 30
    frame_size = int(sr * frame_duration_ms / 1000)

    segments = []
    in_speech = False
    start = 0.0

    for i in range(0, len(pcm) - frame_size + 1, frame_size):
        frame = pcm[i : i + frame_size].tobytes()
        is_speech = vad.is_speech(frame, sr)
        t = i / sr

        if is_speech and not in_speech:
            start = t
            in_speech = True
        elif not is_speech and in_speech:
            segments.append({"start": start, "end": t})
            in_speech = False

    if in_speech:
        segments.append({"start": start, "end": len(pcm) / sr})

    return segments


def segment_audio(
    data: np.ndarray,
    sr: int,
    segments: list[dict[str, float]],
    min_duration: float = 1.5,
    max_duration: float = 25.0,
    merge_gap_ms: float = 300,
) -> list[tuple[np.ndarray, float, float]]:
    """Split audio into clips based on VAD segments."""
    if not segments:
        return []

    # Merge close segments
    merged = [segments[0].copy()]
    for seg in segments[1:]:
        gap = (seg["start"] - merged[-1]["end"]) * 1000
        if gap < merge_gap_ms:
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(seg.copy())

    clips = []
    for seg in merged:
        start_sample = int(seg["start"] * sr)
        end_sample = int(seg["end"] * sr)
        duration = (end_sample - start_sample) / sr

        if duration < min_duration or duration > max_duration:
            continue

        clip_data = data[start_sample:end_sample]
        clips.append((clip_data, seg["start"], seg["end"]))

    return clips


def trim_silence_edges(data: np.ndarray, sr: int, threshold: float = 0.01) -> np.ndarray:
    """Remove leading and trailing silence."""
    mask = np.abs(data) > threshold
    if not mask.any():
        return data
    start = np.argmax(mask)
    end = len(data) - np.argmax(mask[::-1])
    return data[start:end]
