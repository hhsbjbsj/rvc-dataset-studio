"""Single-process Windows task queue with durable source state."""

import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic

from sqlalchemy import select, update

from database import SessionLocal
from processing.rvc_audio import (
    MediaError, ProcessingCancelled, VAD_RATE, WORK_RATE,
    check_space, extract_pcm, make_clips, probe,
)
from processing.rvc_edit import cache_decoded_audio, source_audio_path
from rvc_projects import RvcClip, RvcSource, new_id, source_data, work_dir
from sources.rvc_online import download

logger = logging.getLogger(__name__)


def update_source(db, source_id: str, allowed: tuple[str, ...], **values) -> bool:
    result = db.execute(
        update(RvcSource).where(RvcSource.id == source_id, RvcSource.status.in_(allowed)).values(**values)
    )
    return result.rowcount == 1


class RvcWorker:
    def __init__(self):
        self.stopping = Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rvc")

    def start(self) -> None:
        with SessionLocal.begin() as db:
            db.execute(update(RvcSource).where(RvcSource.status == "running").values(
                status="queued", stage="queued", progress=0,
            ))
            db.execute(update(RvcSource).where(RvcSource.status == "cancelling").values(
                status="cancelled", stage="", progress=0,
            ))
            queued = db.scalars(
                select(RvcSource.id).where(RvcSource.status == "queued").order_by(RvcSource.created_at)
            ).all()
        self.submit(queued)

    def stop(self) -> None:
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def submit(self, source_ids) -> None:
        for source_id in source_ids:
            self.executor.submit(self.process, source_id)

    def process(self, source_id: str) -> None:
        with SessionLocal.begin() as db:
            if not update_source(db, source_id, ("queued",), status="running", stage="probe", progress=1):
                return
            source = db.get(RvcSource, source_id)
            project_id = source.project_id
            data = source_data(source)

        root = work_dir(project_id, source_id)
        last_check = 0.0
        last_progress = -1
        generation = new_id()
        destination = root / generation
        cached_audio = source_audio_path(project_id, source_id)
        cached = committed = False

        def check():
            nonlocal last_check
            if self.stopping.is_set():
                raise ProcessingCancelled
            now = monotonic()
            if now - last_check < 0.5:
                return
            last_check = now
            with SessionLocal() as db:
                status = db.scalar(select(RvcSource.status).where(RvcSource.id == source_id))
            if status != "running":
                raise ProcessingCancelled
            check_space(root)

        def progress(stage: str, percent: int):
            nonlocal last_progress
            if percent <= last_progress:
                return
            with SessionLocal.begin() as db:
                if not update_source(db, source_id, ("running",), stage=stage, progress=percent):
                    raise ProcessingCancelled
            last_progress = percent

        try:
            root.mkdir(parents=True, exist_ok=True)
            self.remove_incomplete_generations(root)
            check()
            with tempfile.TemporaryDirectory(prefix="work-", dir=root) as temp:
                scratch = Path(temp)
                if data["kind"] in {"bilibili", "youtube"}:
                    progress("download", 2)
                    path = download(data["origin"], scratch, check, lambda percent: progress("download", percent))
                else:
                    path = Path(data["origin"])
                    if not path.is_file():
                        raise MediaError("原始素材已移动或无法读取。")
                progress("probe", 40)
                media = probe(path)
                index = data["track_index"] if data["track_index"] is not None else media["default_track"]
                track = next((track for track in media["tracks"] if track["index"] == index), None)
                if track is None:
                    raise MediaError("所选音轨已不存在，请重新选择。")

                duration = track["duration"] or media["duration"]
                options = data["options"]
                split = options["split"]
                if not split and duration > 120:
                    raise MediaError("跳过切分时，单文件最长为 120 秒。请为长素材开启切分。")
                check_space(root, int(duration * (WORK_RATE * 6 + (VAD_RATE * 2 if split else 0))))
                audio = scratch / "decoded.pcm"
                vad = scratch / "vad.pcm" if split else None
                progress("extract", 50)
                extract_pcm(path, index, audio, vad, check)
                progress("segment", 70)
                generated = scratch / "clips"
                generated.mkdir()
                clips = make_clips(audio, vad, generated, options, track["offset"], check)
                progress("quality", 90)
                prepared = scratch / cached_audio.name
                cache_decoded_audio(audio, prepared, check)
                os.replace(prepared, cached_audio)
                cached = True
                os.replace(generated, destination)
                with SessionLocal.begin() as db:
                    if not update_source(
                        db, source_id, ("running",), status="done", stage="", progress=100,
                        media=media, track_index=index, error="",
                    ):
                        raise ProcessingCancelled
                    digests = set(db.scalars(select(RvcClip.digest).where(RvcClip.project_id == project_id)))
                    duplicates = 0
                    for clip in clips:
                        if clip["digest"] in digests:
                            (destination / clip["filename"]).unlink()
                            duplicates += 1
                            continue
                        digests.add(clip["digest"])
                        db.add(RvcClip(project_id=project_id, source_id=source_id, generation=generation, **clip))
                    db.get(RvcSource, source_id).duplicate_clips = duplicates
                committed = True
        except ProcessingCancelled:
            if cached:
                cached_audio.unlink(missing_ok=True)
            self.remove_directory(root, destination)
            with SessionLocal.begin() as db:
                update_source(db, source_id, ("cancelling",), status="cancelled", stage="", progress=0)
        except Exception as exc:
            if committed:
                logger.exception("RVC source %s committed, but scratch cleanup failed", source_id)
                return
            if cached:
                cached_audio.unlink(missing_ok=True)
            self.remove_directory(root, destination)
            logger.exception("RVC source %s failed", source_id)
            with SessionLocal.begin() as db:
                if not update_source(
                    db, source_id, ("running",), status="failed", stage="", error=str(exc)[:750],
                ):
                    update_source(db, source_id, ("cancelling",), status="cancelled", stage="", progress=0)

    @staticmethod
    def remove_directory(root: Path, path: Path) -> None:
        if path.is_dir() and not path.is_symlink() and path.resolve().parent == root.resolve():
            shutil.rmtree(path, ignore_errors=True)

    def remove_incomplete_generations(self, root: Path) -> None:
        # Only sources without committed clips reach processing or startup recovery.
        for path in root.iterdir():
            is_generation = len(path.name) == 32 and all(char in "0123456789abcdef" for char in path.name)
            if is_generation or path.name.startswith("work-"):
                self.remove_directory(root, path)
