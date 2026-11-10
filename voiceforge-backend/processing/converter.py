"""Multi-format audio converter."""

import io
import logging
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from processing.audio_utils import (
    AUDIO_EXTENSIONS,
    load_audio,
    resample,
    run_ffmpeg,
    save_audio,
    to_mono,
)
from processing.normalizer import normalize_audio

logger = logging.getLogger(__name__)


def unique_path(path: Path) -> Path:
    """Return `path`, or `name_1.ext`, `name_2.ext`… if it already exists, so outputs
    from different inputs (take.wav and take.mp3, two Parquet shards) never overwrite."""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _ensure_inside(root: Path, member_name: str) -> None:
    """Reject archive entries that would land outside `root` ("zip slip")."""
    root = root.resolve()
    target = (root / member_name).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Unsafe path in archive: {member_name}")


def convert_file(
    input_path: Path,
    output_dir: Path,
    target_format: str = "wav",
    sample_rate: int = 22050,
    bit_depth: int = 16,
    channels: str = "mono",
    normalize: bool = True,
    trim_silence: bool = False,
) -> list[Path]:
    """Convert a single file (or archive) to target format. Returns list of output paths."""
    suffix = input_path.suffix.lower()
    # Path.suffix of "x.tar.gz" is ".gz", so check the full name for tarballs.
    is_tarball = input_path.name.lower().endswith((".tar.gz", ".tgz", ".tar", ".tar.bz2", ".tar.xz"))

    if suffix == ".parquet":
        return _convert_parquet(input_path, output_dir, sample_rate, bit_depth, channels, normalize)
    if is_tarball:
        return _convert_archive(input_path, output_dir, target_format, sample_rate, bit_depth, channels, normalize, trim_silence, tar=True)
    if suffix == ".zip":
        return _convert_archive(input_path, output_dir, target_format, sample_rate, bit_depth, channels, normalize, trim_silence, tar=False)
    if suffix == ".arrow":
        return _convert_arrow(input_path, output_dir, sample_rate, bit_depth, channels, normalize)
    if suffix == ".npy":
        return _convert_npy(input_path, output_dir, sample_rate, bit_depth, channels, normalize)
    if suffix in (".hdf5", ".h5"):
        return _convert_hdf5(input_path, output_dir, sample_rate, bit_depth, channels, normalize)
    if suffix == ".7z":
        return _convert_7z(input_path, output_dir, target_format, sample_rate, bit_depth, channels, normalize, trim_silence)

    return [_convert_single(input_path, output_dir, target_format, sample_rate, bit_depth, channels, normalize, trim_silence)]


def _convert_single(
    input_path: Path,
    output_dir: Path,
    target_format: str,
    sample_rate: int,
    bit_depth: int,
    channels: str,
    normalize: bool,
    trim_silence: bool,
) -> Path:
    output_path = unique_path(output_dir / f"{input_path.stem}.{target_format}")
    suffix = input_path.suffix.lower()

    if suffix == ".wav" and target_format == "wav":
        data, sr = load_audio(input_path)
    elif suffix in AUDIO_EXTENSIONS or suffix in (".mp4", ".mkv", ".webm"):
        tmp_wav = output_dir / f"_tmp_{input_path.stem}.wav"
        ac = "1" if channels == "mono" else "2"
        subtype = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}.get(bit_depth, "pcm_s16le")
        base_args = ["-i", str(input_path), "-vn", "-acodec", subtype, "-ar", str(sample_rate), "-ac", ac]
        try:
            try:
                # SoX resampler sounds best, but many FFmpeg builds ship without libsoxr
                run_ffmpeg(base_args + ["-af", "aresample=resampler=soxr", str(tmp_wav)])
            except RuntimeError:
                run_ffmpeg(base_args + [str(tmp_wav)])
            data, sr = load_audio(tmp_wav)
        finally:
            tmp_wav.unlink(missing_ok=True)
    else:
        raise ValueError(f"Unsupported format: {suffix}")

    if channels == "mono":
        data = to_mono(data) if data.ndim > 1 else data
    if sr != sample_rate:
        data = resample(data, sr, sample_rate)
        sr = sample_rate
    if normalize:
        data = normalize_audio(data)
    if trim_silence:
        from processing.vad import trim_silence_edges
        data = trim_silence_edges(data, sr)

    save_audio(output_path, data, sr, bit_depth)
    return output_path


def _convert_parquet(
    input_path: Path,
    output_dir: Path,
    sample_rate: int,
    bit_depth: int,
    channels: str,
    normalize: bool,
) -> list[Path]:
    from processing.parquet_reader import extract_audio_from_parquet

    return extract_audio_from_parquet(input_path, output_dir, sample_rate, bit_depth, channels, normalize)


def _convert_arrow(
    input_path: Path,
    output_dir: Path,
    sample_rate: int,
    bit_depth: int,
    channels: str,
    normalize: bool,
) -> list[Path]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    with pa.memory_map(str(input_path), "r") as source:
        table = pa.ipc.open_file(source).read_all()
    tmp = output_dir / "_tmp.parquet"
    pq.write_table(table, str(tmp))
    results = _convert_parquet(tmp, output_dir, sample_rate, bit_depth, channels, normalize)
    tmp.unlink(missing_ok=True)
    return results


def _convert_npy(
    input_path: Path,
    output_dir: Path,
    sample_rate: int,
    bit_depth: int,
    channels: str,
    normalize: bool,
) -> list[Path]:
    data = np.load(str(input_path)).astype(np.float32)
    if channels == "mono" and data.ndim > 1:
        data = data.mean(axis=1)
    if normalize:
        data = normalize_audio(data)
    out = unique_path(output_dir / f"{input_path.stem}.wav")
    save_audio(out, data, sample_rate, bit_depth)
    return [out]


def _convert_hdf5(
    input_path: Path,
    output_dir: Path,
    sample_rate: int,
    bit_depth: int,
    channels: str,
    normalize: bool,
) -> list[Path]:
    import h5py

    results = []
    with h5py.File(str(input_path), "r") as f:
        for key in f.keys():
            data = f[key][()].astype(np.float32)
            if channels == "mono" and data.ndim > 1:
                data = data.mean(axis=1)
            if normalize:
                data = normalize_audio(data)
            out = unique_path(output_dir / f"{Path(key).name}.wav")
            save_audio(out, data, sample_rate, bit_depth)
            results.append(out)
    return results


def _convert_7z(
    input_path: Path,
    output_dir: Path,
    target_format: str,
    sample_rate: int,
    bit_depth: int,
    channels: str,
    normalize: bool,
    trim_silence: bool,
) -> list[Path]:
    import py7zr

    extract_dir = unique_path(output_dir / "_7z_extract")
    extract_dir.mkdir(exist_ok=True)
    with py7zr.SevenZipFile(str(input_path), mode="r") as z:
        for name in z.getnames():
            _ensure_inside(extract_dir, name)
        z.extractall(path=str(extract_dir))
    results = []
    for f in extract_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
            results.extend(convert_file(f, output_dir, target_format, sample_rate, bit_depth, channels, normalize, trim_silence))
    return results


def _convert_archive(
    input_path: Path,
    output_dir: Path,
    target_format: str,
    sample_rate: int,
    bit_depth: int,
    channels: str,
    normalize: bool,
    trim_silence: bool,
    tar: bool = False,
) -> list[Path]:
    extract_dir = unique_path(output_dir / "_extract")
    extract_dir.mkdir(exist_ok=True)

    if tar:
        with tarfile.open(str(input_path), "r:*") as tf:
            members = []
            for m in tf.getmembers():
                # Only plain files and folders: no symlinks, devices or paths outside extract_dir
                if not (m.isfile() or m.isdir()):
                    continue
                _ensure_inside(extract_dir, m.name)
                members.append(m)
            if hasattr(tarfile, "data_filter"):
                tf.extractall(str(extract_dir), members=members, filter="data")
            else:
                tf.extractall(str(extract_dir), members=members)
    else:
        with zipfile.ZipFile(str(input_path), "r") as zf:
            for name in zf.namelist():
                _ensure_inside(extract_dir, name)
            zf.extractall(str(extract_dir))

    results = []
    for f in extract_dir.rglob("*"):
        if f.is_file():
            suffix = f.suffix.lower()
            if suffix in AUDIO_EXTENSIONS or suffix in (".parquet", ".npy", ".arrow", ".hdf5", ".h5"):
                try:
                    results.extend(convert_file(f, output_dir, target_format, sample_rate, bit_depth, channels, normalize, trim_silence))
                except Exception as e:
                    logger.warning("Failed to convert %s: %s", f, e)
    return results
