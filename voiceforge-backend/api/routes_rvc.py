"""Loopback-only HTTP endpoints for mixed-source RVC projects."""

import os
import stat
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.types import Message, Receive, Scope, Send

from config import rvc_export_root
from database import Job, RemovedHistoryEntry, SessionLocal, get_db
from processing.rvc_audio import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, MediaError, probe
from processing.rvc_edit import (
    create_edit, create_edits, delete_buffered_clips, restore_buffer_audio, source_audio_info, source_audio_path, waveform_peaks,
)
from processing.rvc_export import (
    bundle_exports, discard_export_bundle, export_bundle_path, export_dataset, find_batch_archives, find_export_archive,
    list_exports, rename_export,
)
from processing.rvc_import import stage_conversion
from rvc_projects import (
    RvcBundleSelection, RvcClip, RvcExportDownload, RvcExportSelection, RvcMergeBuffer, RvcProject, RvcSource, RvcWorkspace,
    active_clip, clip_data, clip_path, new_id, project_data, source_data, work_dir,
)
from sources.rvc_online import resolve_source_url, search_bilibili_page
from workers.rvc_worker import update_source

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
AVAILABLE = ("ready", "failed", "cancelled")
SourceKind = Literal["local_video", "local_audio"]
SampleRate = Literal[16000, 22050, 24000, 32000, 40000, 44100, 48000]
Identifier = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
picker_lock = Lock()
conversion_import_lock = Lock()
# Exports must finish reading selected WAVs before a merge removes them.
clip_file_lock = Lock()
export_history_lock = Lock()
workspace_lock = Lock()
FINISHED_JOBS = {"done", "failed", "cancelled", "canceled"}
BUNDLE_SELECTION_TTL = timedelta(days=1)


def require_local(request: Request) -> None:
    origin = request.headers.get("origin")
    if request.url.hostname not in LOOPBACK or (origin is not None and urlsplit(origin).hostname not in LOOPBACK):
        raise HTTPException(403, "RVC 项目仅可从本机访问。")


router = APIRouter(prefix="/api/rvc", tags=["RVC projects"], dependencies=[Depends(require_local)])
DB = Annotated[Session, Depends(get_db)]


class ProjectInput(BaseModel):
    series: Name
    character: Name
    language: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    target_minutes: float = Field(20, gt=0, le=1440)


class WorkspaceInput(BaseModel):
    project_id: Identifier


class ExportHistoryEntry(BaseModel):
    project_id: Identifier
    export_id: Identifier


class HistoryRemoval(BaseModel):
    exports: list[ExportHistoryEntry] = Field(default_factory=list)
    job_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=200)


class ExportBatchList(BaseModel):
    exports: list[ExportHistoryEntry] = Field(min_length=1)


class ExportBundleInput(ExportBatchList):
    deselect_after_download: bool = False


class ExportSelectionInput(ExportBatchList):
    selected: bool


class LocalImport(BaseModel):
    kind: SourceKind
    paths: list[Annotated[str, StringConstraints(min_length=1, max_length=32767)]] = Field(min_length=1, max_length=1000)
    recursive: bool = False


class ConversionImport(BaseModel):
    split: bool = False


class PickerInput(BaseModel):
    kind: SourceKind
    folder: bool = False
    locale: Literal["zh-CN", "en", "ja"] = "zh-CN"


class OnlineVideo(BaseModel):
    url: str = Field(max_length=2048)
    title: str = Field("", max_length=500)
    uploader: str = Field("", max_length=300)
    duration: float = Field(0, ge=0, le=1_000_000)


class OnlineImport(BaseModel):
    videos: list[OnlineVideo] = Field(min_length=1, max_length=100)


class TrackInput(BaseModel):
    index: int = Field(ge=0)


class ProcessInput(BaseModel):
    source_ids: list[Identifier] | None = Field(None, max_length=1000)
    split: bool = True
    min_seconds: float = Field(1.5, ge=0.15, le=60)
    max_seconds: float = Field(15, ge=0.3, le=120)
    normalize: bool = False

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min_seconds > self.max_seconds:
            raise ValueError("最短片段时长不能超过最长时长。")
        return self


class ReviewInput(BaseModel):
    ids: list[Identifier] | None = Field(None, min_length=1, max_length=1000)
    review: Literal["kept", "excluded", "pending"]


class ClipRange(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class ClipRanges(BaseModel):
    ranges: list[ClipRange] = Field(min_length=1, max_length=50)


class MergeInput(BaseModel):
    clip_ids: list[Identifier] = Field(min_length=2, max_length=1000)


class ClearBufferInput(BaseModel):
    clip_ids: list[Identifier] = Field(min_length=1)


BatchName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=36)]


class ExportInput(BaseModel):
    sample_rate: SampleRate = 40000
    clip_ids: list[Identifier] | None = Field(None, min_length=1, max_length=1000)
    batch_name: BatchName = ""


class ExportNameInput(BaseModel):
    batch_name: BatchName


def get_project(db: Session, project_id: str) -> RvcProject:
    project = db.get(RvcProject, project_id)
    if project is None:
        raise HTTPException(404, "角色项目不存在。")
    return project


def get_source(db: Session, project_id: str, source_id: str) -> RvcSource:
    source = db.get(RvcSource, source_id)
    if source is None or source.project_id != project_id:
        raise HTTPException(404, "素材不存在。")
    return source


def get_editable_source(db: Session, project_id: str, source_id: str) -> RvcSource:
    source = get_source(db, project_id, source_id)
    if source.status != "done":
        raise HTTPException(409, "请先处理这项素材，再编辑原音轨。")
    return source


def add_source(db: Session, **fields) -> bool:
    try:
        with db.begin_nested():
            db.add(RvcSource(**fields))
            db.flush()
        return True
    except IntegrityError:
        return False


@router.get("/projects")
def projects(db: DB):
    return [project_data(row) for row in db.scalars(select(RvcProject).order_by(RvcProject.created_at.desc()))]


@router.get("/workspace")
def workspace(db: DB):
    state = db.get(RvcWorkspace, 1)
    return {"last_project_id": state.project_id if state else None}


@router.put("/workspace")
def remember_workspace(data: WorkspaceInput, db: DB):
    with workspace_lock:
        get_project(db, data.project_id)
        db.merge(RvcWorkspace(id=1, project_id=data.project_id))
        db.commit()
    return {"last_project_id": data.project_id}


@router.post("/projects", status_code=201)
def create_project(data: ProjectInput, db: DB):
    project = RvcProject(**data.model_dump())
    db.add(project)
    db.commit()
    return project_data(project)


@router.get("/projects/{project_id}")
def project_details(project_id: Identifier, db: DB):
    project = get_project(db, project_id)
    sources = db.scalars(select(RvcSource).where(RvcSource.project_id == project_id).order_by(RvcSource.created_at)).all()
    stats = db.execute(
        select(RvcClip.review, func.count(), func.sum(RvcClip.duration))
        .where(RvcClip.project_id == project_id, active_clip()).group_by(RvcClip.review)
    ).all()
    return {
        **project_data(project), "sources": [source_data(source) for source in sources],
        "review": {review: {"count": count, "seconds": round(seconds, 2)} for review, count, seconds in stats},
    }


@router.post("/picker")
def choose_paths(data: PickerInput):
    import tkinter as tk
    from tkinter import filedialog

    if os.name != "nt":
        raise HTTPException(400, "系统文件对话框只在 Windows 上启用，请直接填写路径。")
    if not picker_lock.acquire(blocking=False):
        raise HTTPException(409, "文件选择对话框已经打开。")
    root = None
    try:
        folder_title, files_title, media_label, all_label = {
            "zh-CN": ("选择包含素材的文件夹", "选择本地媒体素材", "媒体文件", "所有文件"),
            "en": ("Choose a source folder", "Choose local media", "Media files", "All files"),
            "ja": ("素材のフォルダーを選択", "ローカルメディアを選択", "メディアファイル", "すべてのファイル"),
        }[data.locale]
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if data.folder:
            selected = filedialog.askdirectory(parent=root, mustexist=True, title=folder_title)
            return {"paths": [selected] if selected else []}
        extensions = VIDEO_EXTENSIONS if data.kind == "local_video" else AUDIO_EXTENSIONS
        filetypes = [(media_label, " ".join("*" + ext for ext in sorted(extensions))), (all_label, "*.*")]
        return {"paths": list(filedialog.askopenfilenames(parent=root, title=files_title, filetypes=filetypes))}
    except tk.TclError as exc:
        raise HTTPException(400, "无法打开系统文件对话框，请直接填写完整路径。") from exc
    finally:
        if root is not None:
            root.destroy()
        picker_lock.release()


def local_paths(values: list[str], extensions: set[str], recursive: bool, errors: list[dict]):
    def on_error(exc):
        errors.append({"origin": exc.filename or "", "error": str(exc)})

    for value in values:
        try:
            path = Path(value.strip().strip('"'))
            if not path.is_absolute():
                raise ValueError("请填写绝对路径。")
            path = path.resolve(strict=True)
            if os.name == "nt" and path.drive.startswith(chr(92) * 2) and not path.drive.startswith(chr(92) * 2 + "?"):
                raise ValueError("请选择本机磁盘上的文件。")
            if path.is_file():
                if path.suffix.lower() not in extensions:
                    raise ValueError("当前入口不支持此文件扩展名。")
                yield path
                continue
            if not path.is_dir():
                raise ValueError("路径不是可读的文件或文件夹。")
            for directory, subdirs, files in os.walk(path, followlinks=False, onerror=on_error):
                parent = Path(directory)
                if not recursive:
                    subdirs.clear()
                elif os.name == "nt":
                    for name in subdirs[:]:
                        try:
                            attrs = getattr((parent / name).lstat(), "st_file_attributes", 0)
                            if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                                subdirs.remove(name)
                        except OSError:
                            subdirs.remove(name)
                for name in sorted(files):
                    candidate = parent / name
                    if candidate.suffix.lower() in extensions and not candidate.is_symlink() and candidate.is_file():
                        yield candidate
        except (OSError, ValueError) as exc:
            errors.append({"origin": value, "error": str(exc)})


@router.post("/projects/{project_id}/sources/local")
def import_local(project_id: Identifier, data: LocalImport, db: DB):
    get_project(db, project_id)
    extensions = VIDEO_EXTENSIONS if data.kind == "local_video" else AUDIO_EXTENSIONS
    errors = []
    staged = {}
    duplicate = 0
    existing = set(db.scalars(select(RvcSource.source_key).where(RvcSource.project_id == project_id)))
    for index, path in enumerate(local_paths(data.paths, extensions, data.recursive, errors)):
        if index >= 1000:
            errors.append({"origin": str(path), "error": "一次最多扫描 1000 个素材文件。"})
            break
        key = os.path.normcase(str(path))
        if key in existing or key in staged:
            duplicate += 1
            continue
        try:
            media = probe(path)
            if (data.kind == "local_video") != media["has_video"]:
                raise MediaError("媒体类型与导入入口不匹配。")
            staged[key] = (path, media)
        except (MediaError, OSError) as exc:
            errors.append({"origin": str(path), "error": str(exc)})
    added = 0
    for key, (path, media) in staged.items():
        added += add_source(db, project_id=project_id, kind=data.kind, source_key=key, origin=str(path),
                            title=path.name, media=media, track_index=media["default_track"])
    db.commit()
    return {"added": added, "duplicates": duplicate + len(staged) - added, "errors": errors}


@router.post("/projects/{project_id}/sources/converted/{conversion_id}", status_code=202)
def import_conversion(project_id: Identifier, conversion_id: UUID, data: ConversionImport, request: Request, db: DB):
    get_project(db, project_id)
    job = db.get(Job, str(conversion_id))
    if job is None or job.query_type != "conversion" or job.status != "done" or not job.output_path:
        raise HTTPException(404, "找不到可导入的转换结果。")
    archive = Path(job.output_path)
    db.close()
    destination = work_dir(project_id, "converted") / str(conversion_id)
    options = ProcessInput(split=data.split).model_dump(exclude={"source_ids"})
    queued = []
    with conversion_import_lock:
        try:
            files = stage_conversion(archive, destination)
        except FileNotFoundError as exc:
            raise HTTPException(404, "转换文件已不存在。") from exc
        except MediaError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (OSError, BadZipFile) as exc:
            raise HTTPException(400, "无法读取或保存转换文件。") from exc

        existing = set(db.scalars(select(RvcSource.source_key).where(RvcSource.project_id == project_id)))
        for path, title in files:
            key = os.path.normcase(str(path))
            if key in existing:
                continue
            source_id = new_id()
            if add_source(db, id=source_id, project_id=project_id, kind="local_audio", source_key=key,
                          origin=str(path), title=title, status="queued", stage="queued", options=options):
                queued.append(source_id)
        db.commit()
    request.app.state.rvc_worker.submit(queued)
    return {"queued": len(queued), "duplicates": len(files) - len(queued)}


@router.get("/search/bilibili")
def search_bilibili(q: Annotated[str, Query(min_length=1, max_length=250)], page: Annotated[int, Query(ge=1, le=100)] = 1):
    try:
        return search_bilibili_page(q.strip(), page)
    except MediaError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/projects/{project_id}/sources/online")
def import_online(project_id: Identifier, data: OnlineImport, db: DB):
    get_project(db, project_id)
    errors = []
    added = 0
    for video in data.videos:
        try:
            platform, url = resolve_source_url(video.url)
        except ValueError as exc:
            errors.append({"origin": video.url, "error": str(exc)})
            continue
        added += add_source(db, project_id=project_id, kind=platform, source_key=url, origin=url,
                            title=video.title or url, uploader=video.uploader, media={"duration": video.duration})
    db.commit()
    return {"added": added, "duplicates": len(data.videos) - len(errors) - added, "errors": errors}


@router.patch("/projects/{project_id}/sources/{source_id}/track")
def choose_track(project_id: Identifier, source_id: Identifier, data: TrackInput, db: DB):
    source = get_source(db, project_id, source_id)
    if data.index not in {track["index"] for track in source.media.get("tracks", [])}:
        raise HTTPException(400, "选定音轨不存在。")
    if not update_source(db, source_id, AVAILABLE, track_index=data.index):
        raise HTTPException(409, "仅能为尚未成功处理的素材选择音轨。")
    db.commit()
    return {"track_index": data.index}


@router.post("/projects/{project_id}/process", status_code=202)
def process_project(project_id: Identifier, data: ProcessInput, request: Request, db: DB):
    get_project(db, project_id)
    filters = [RvcSource.project_id == project_id, RvcSource.status.in_(AVAILABLE)]
    if data.source_ids is not None:
        filters.append(RvcSource.id.in_(data.source_ids))
    candidates = db.scalars(select(RvcSource.id).where(*filters).order_by(RvcSource.created_at)).all()
    options = data.model_dump(exclude={"source_ids"})
    queued = [
        source_id for source_id in candidates
        if update_source(db, source_id, AVAILABLE, status="queued", stage="queued", progress=0, error="", options=options)
    ]
    db.commit()
    request.app.state.rvc_worker.submit(queued)
    return {"queued": len(queued)}


@router.post("/projects/{project_id}/sources/{source_id}/cancel")
def cancel_source(project_id: Identifier, source_id: Identifier, db: DB):
    get_source(db, project_id, source_id)
    if update_source(db, source_id, ("queued",), status="cancelled", stage="", progress=0):
        status = "cancelled"
    elif update_source(db, source_id, ("running",), status="cancelling"):
        status = "cancelling"
    else:
        raise HTTPException(409, "该素材当前无法取消。")
    db.commit()
    return {"status": status}


@router.post("/projects/{project_id}/sources/{source_id}/prepare-audio")
def prepare_audio(project_id: Identifier, source_id: Identifier, db: DB):
    source = get_editable_source(db, project_id, source_id)
    try:
        return source_audio_info(source)
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_id}/sources/{source_id}/original-audio")
def original_audio(project_id: Identifier, source_id: Identifier, db: DB):
    get_editable_source(db, project_id, source_id)
    path = source_audio_path(project_id, source_id)
    if not path.is_file():
        raise HTTPException(404, "请先载入原音轨。")
    return FileResponse(path, media_type="audio/flac")


@router.get("/projects/{project_id}/sources/{source_id}/waveform")
def source_waveform(project_id: Identifier, source_id: Identifier, db: DB):
    get_editable_source(db, project_id, source_id)
    path = source_audio_path(project_id, source_id)
    if not path.is_file():
        raise HTTPException(404, "请先载入原音轨。")
    try:
        return {"peaks": waveform_peaks(path)}
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_id}/sources/{source_id}/clips")
def cut_clip(project_id: Identifier, source_id: Identifier, data: ClipRange | ClipRanges, db: DB):
    with clip_file_lock:
        source = get_editable_source(db, project_id, source_id)
        return save_ranges(db, source, data)


@router.post("/projects/{project_id}/clips/{clip_id}/trim")
def trim_existing_clip(project_id: Identifier, clip_id: Identifier, data: ClipRange | ClipRanges, db: DB):
    with clip_file_lock:
        clip = db.scalar(select(RvcClip).where(RvcClip.project_id == project_id, RvcClip.id == clip_id, active_clip()))
        if clip is None:
            raise HTTPException(404, "请在审核列表中选择要截取的片段。")
        source = get_editable_source(db, project_id, clip.source_id)
        return save_ranges(db, source, data, clip=clip)


def save_ranges(db: DB, source: RvcSource, data: ClipRange | ClipRanges, *, clip: RvcClip | None = None):
    ranges = data.ranges if isinstance(data, ClipRanges) else [data]
    try:
        results = create_edits(
            db, source, [(item.start, item.end) for item in ranges], "cut" if clip is None else "trim", clip=clip,
        )
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"clips": results} if isinstance(data, ClipRanges) else results[0]


@router.post("/projects/{project_id}/clips/merge")
def merge_clips(project_id: Identifier, data: MergeInput, db: DB):
    with clip_file_lock:
        ids = set(data.clip_ids)
        clips = db.scalars(select(RvcClip).where(RvcClip.project_id == project_id, RvcClip.id.in_(ids), active_clip())).all()
        if len(clips) != len(ids) or len(clips) < 2:
            raise HTTPException(400, "请选择当前项目中仍存在的至少两个片段。")
        source_ids = {clip.source_id for clip in clips}
        if len(source_ids) != 1:
            raise HTTPException(400, "请选择同一项素材里的片段。")
        source = get_editable_source(db, project_id, clips[0].source_id)
        try:
            return create_edit(
                db, source, min(clip.start for clip in clips), max(clip.end for clip in clips), "merge", replace=clips,
            )
        except MediaError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_id}/merge-buffer")
def merge_buffer(project_id: Identifier, db: DB):
    get_project(db, project_id)
    rows = db.execute(
        select(RvcClip, RvcSource).join(RvcSource).join(RvcMergeBuffer)
        .where(RvcClip.project_id == project_id)
        .order_by(RvcMergeBuffer.created_at.desc(), RvcClip.start, RvcClip.id)
    )
    return {"clips": [clip_data(clip, source) for clip, source in rows]}


@router.delete("/projects/{project_id}/merge-buffer")
def clear_merge_buffer(project_id: Identifier, data: ClearBufferInput, db: DB):
    with clip_file_lock:
        get_project(db, project_id)
        confirmed = set(data.clip_ids)
        buffered = db.execute(
            select(RvcClip, RvcMergeBuffer).join(RvcMergeBuffer)
            .where(RvcClip.project_id == project_id)
        )
        rows = [(clip, entry) for clip, entry in buffered if clip.id in confirmed]
        try:
            delete_buffered_clips(db, rows)
        except MediaError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"deleted": len(rows)}


def get_buffer_entry(db: Session, project_id: str, clip_id: str) -> tuple[RvcClip, RvcMergeBuffer]:
    row = db.execute(
        select(RvcClip, RvcMergeBuffer).join(RvcMergeBuffer)
        .where(RvcClip.project_id == project_id, RvcClip.id == clip_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "该片段不在此项目的合并缓冲区中。")
    clip, entry = row
    return clip, entry


@router.post("/projects/{project_id}/merge-buffer/{clip_id}/restore")
def restore_merged_clip(project_id: Identifier, clip_id: Identifier, db: DB):
    with clip_file_lock:
        clip, entry = get_buffer_entry(db, project_id, clip_id)
        restore_buffer_audio(clip)
        db.delete(entry)
        db.commit()
    return {"id": clip_id}


@router.delete("/projects/{project_id}/merge-buffer/{clip_id}")
def delete_merged_clip(project_id: Identifier, clip_id: Identifier, db: DB):
    with clip_file_lock:
        clip, entry = get_buffer_entry(db, project_id, clip_id)
        try:
            delete_buffered_clips(db, [(clip, entry)])
        except MediaError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"id": clip_id}


@router.get("/projects/{project_id}/clips")
def clips(project_id: Identifier, db: DB, page: Annotated[int, Query(ge=1)] = 1):
    get_project(db, project_id)
    filtered = select(RvcClip, RvcSource).join(RvcSource).where(RvcClip.project_id == project_id, active_clip())
    rows = db.execute(filtered.order_by(RvcSource.created_at, RvcClip.start, RvcClip.id).limit(100).offset((page - 1) * 100))
    total = db.scalar(select(func.count()).select_from(RvcClip).where(RvcClip.project_id == project_id, active_clip()))
    return {"clips": [clip_data(clip, source) for clip, source in rows], "total": total}


@router.get("/projects/{project_id}/clips/{clip_id}/audio")
def clip_audio(project_id: Identifier, clip_id: Identifier, db: DB):
    clip = db.get(RvcClip, clip_id)
    if clip is None or clip.project_id != project_id:
        raise HTTPException(404, "片段文件不存在。")
    path = clip_path(clip)
    if not path.is_file() and db.get(RvcMergeBuffer, clip_id) is not None:
        path = path.with_suffix(".deleting")
    if not path.is_file():
        raise HTTPException(404, "片段文件不存在。")
    return FileResponse(path, media_type="audio/wav")


@router.get("/projects/{project_id}/clips/{clip_id}/waveform")
def clip_waveform(project_id: Identifier, clip_id: Identifier, db: DB):
    clip = db.scalar(select(RvcClip).where(RvcClip.project_id == project_id, RvcClip.id == clip_id, active_clip()))
    if clip is None:
        raise HTTPException(404, "片段不存在。")
    try:
        return {"peaks": waveform_peaks(clip_path(clip))}
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/projects/{project_id}/clips/review")
def review_clips(project_id: Identifier, data: ReviewInput, db: DB):
    get_project(db, project_id)
    filters = [RvcClip.project_id == project_id, active_clip()]
    if data.ids is None:
        filters.append(RvcClip.review == "pending")
    else:
        filters.append(RvcClip.id.in_(data.ids))
    result = db.execute(update(RvcClip).where(*filters).values(review=data.review))
    db.commit()
    return {"updated": result.rowcount}


@router.post("/projects/{project_id}/export")
def export_project(project_id: Identifier, data: ExportInput, db: DB):
    with clip_file_lock:
        project = project_data(get_project(db, project_id))
        selected = set(data.clip_ids) if data.clip_ids is not None else None
        selection = RvcClip.review == "kept" if selected is None else RvcClip.id.in_(selected)
        rows = db.execute(
            select(RvcClip, RvcSource).join(RvcSource)
            .where(RvcClip.project_id == project_id, selection, active_clip())
            .order_by(RvcSource.created_at, RvcClip.start, RvcClip.id)
        ).all()
        if selected is not None and len(rows) != len(selected):
            raise HTTPException(400, "部分所选片段已不存在或不属于此项目，请重新选择。")
        entries = [
            {"clip": clip_data(clip, source), "source": source_data(source), "path": clip_path(clip)}
            for clip, source in rows
        ]
        db.close()
        try:
            return export_dataset(
                project, entries, data.sample_rate, scope="kept" if selected is None else "selected",
                batch_name=data.batch_name,
            )
        except MediaError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/exports")
def rvc_export_history(db: DB):
    projects = [project_data(row) for row in db.scalars(select(RvcProject))]
    removed_by_project = defaultdict(set)
    for project_id, export_id in db.execute(
        select(RemovedHistoryEntry.project_id, RemovedHistoryEntry.item_id).where(RemovedHistoryEntry.kind == "export")
    ):
        removed_by_project[project_id].add(export_id)
    downloaded_by_project = defaultdict(set)
    for project_id, export_id in db.execute(select(RvcExportDownload.project_id, RvcExportDownload.export_id)):
        downloaded_by_project[project_id].add(export_id)
    db.close()
    history = []
    with export_history_lock:
        for project in projects:
            project_id = project["id"]
            batches = list_exports(project_id, removed_by_project[project_id], downloaded_by_project[project_id])
            if batches:
                history.append({"project": project, "batches": batches})
    return sorted(history, key=lambda entry: entry["batches"][0]["created_at"], reverse=True)


@router.get("/projects/{project_id}/exports")
def export_history(project_id: Identifier, db: DB):
    get_project(db, project_id)
    removed_ids = set(db.scalars(select(RemovedHistoryEntry.item_id).where(
        RemovedHistoryEntry.kind == "export", RemovedHistoryEntry.project_id == project_id,
    )))
    downloaded_ids = set(db.scalars(select(RvcExportDownload.export_id).where(RvcExportDownload.project_id == project_id)))
    with export_history_lock:
        return list_exports(project_id, removed_ids, downloaded_ids)


@router.get("/exports/selection")
def saved_export_selection(db: DB) -> list[ExportHistoryEntry]:
    rows = db.execute(select(RvcExportSelection.project_id, RvcExportSelection.export_id))
    return [ExportHistoryEntry(project_id=project_id, export_id=export_id) for project_id, export_id in rows]


@router.patch("/exports/selection")
def update_export_selection(data: ExportSelectionInput, db: DB) -> list[ExportHistoryEntry]:
    requested = {(entry.project_id, entry.export_id) for entry in data.exports}
    with export_history_lock:
        current = {(row.project_id, row.export_id): row for row in db.scalars(select(RvcExportSelection))}
        if data.selected:
            additions = defaultdict(set)
            for project_id, export_id in requested - current.keys():
                additions[project_id].add(export_id)
            for project_id, ids in additions.items():
                get_project(db, project_id)
                removed = db.scalars(select(RemovedHistoryEntry.item_id).where(
                    RemovedHistoryEntry.kind == "export", RemovedHistoryEntry.project_id == project_id,
                ))
                if not ids.isdisjoint(removed) or find_batch_archives(project_id, ids).keys() != ids:
                    raise HTTPException(404, "部分所选导出已不存在，请刷新历史记录。")
                db.add_all(RvcExportSelection(project_id=project_id, export_id=export_id) for export_id in ids)
        else:
            for key in requested & current.keys():
                db.delete(current[key])
        db.commit()
        return saved_export_selection(db)


@router.post("/exports/bundles", status_code=201)
def create_export_bundle(data: ExportBundleInput, db: DB):
    requested = dict.fromkeys((entry.project_id, entry.export_id) for entry in data.exports)
    grouped = defaultdict(set)
    for project_id, export_id in requested:
        grouped[project_id].add(export_id)
    missing = "部分所选导出已不存在，请刷新历史记录。"
    try:
        with ExitStack() as stack:
            with export_history_lock:
                found = {}
                for project_id, ids in grouped.items():
                    get_project(db, project_id)
                    removed = db.scalars(select(RemovedHistoryEntry.item_id).where(
                        RemovedHistoryEntry.kind == "export", RemovedHistoryEntry.project_id == project_id,
                    ))
                    if not ids.isdisjoint(removed):
                        raise HTTPException(404, missing)
                    batches = find_batch_archives(project_id, ids)
                    if batches.keys() != ids:
                        raise HTTPException(404, missing)
                    found[project_id] = batches
                archives = []
                for project_id, export_id in requested:
                    name, path = found[project_id][export_id]
                    archives.append((name, stack.enter_context(ZipFile(path))))
            db.close()
            bundle = bundle_exports(archives)
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (BadZipFile, NotImplementedError, RuntimeError, ValueError) as exc:
        raise HTTPException(400, "无法读取所选批次的 ZIP。") from exc
    except OSError as exc:
        raise HTTPException(409, "无法打包所选批次，请检查导出 ZIP 和工作盘。") from exc
    download_url = f"/api/rvc/exports/bundles/{bundle.stem}"
    try:
        cutoff = datetime.now(timezone.utc) - BUNDLE_SELECTION_TTL
        db.execute(delete(RvcBundleSelection).where(RvcBundleSelection.created_at < cutoff))
        db.add(RvcBundleSelection(
            bundle_id=bundle.stem,
            exports=[{"project_id": project_id, "export_id": export_id} for project_id, export_id in requested],
            deselect_after_download=data.deselect_after_download,
        ))
        db.commit()
    except SQLAlchemyError as exc:
        discard_export_bundle(bundle)
        raise HTTPException(500, "无法保存打包下载记录。") from exc
    return {"download_url": download_url, "status_url": f"{download_url}/status"}


def finish_bundle_download(bundle_id: str, delivered: bool) -> None:
    with export_history_lock, SessionLocal.begin() as db:
        bundle = db.get(RvcBundleSelection, bundle_id)
        if bundle is None or bundle.status == "completed":
            return
        if delivered:
            for entry in bundle.exports:
                key = (entry["project_id"], entry["export_id"])
                if db.get(RvcExportDownload, key) is None:
                    db.add(RvcExportDownload(project_id=key[0], export_id=key[1]))
                if bundle.deselect_after_download:
                    selected = db.get(RvcExportSelection, key)
                    if selected is not None:
                        db.delete(selected)
        bundle.status = "completed" if delivered else "interrupted"
    if delivered:
        discard_export_bundle(export_bundle_path(bundle_id))


class TrackedBundleDownload(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        full_response = False
        delivered = False

        async def track_send(message: Message) -> None:
            nonlocal full_response, delivered
            await send(message)
            if message["type"] == "http.response.start":
                full_response = message["status"] == 200
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                delivered = full_response

        # Observe the final response body even on servers that offer delegated file sending.
        extensions = {key: value for key, value in scope.get("extensions", {}).items()
                      if key != "http.response.pathsend"}
        try:
            await super().__call__({**scope, "extensions": extensions}, receive, track_send)
        finally:
            await run_in_threadpool(finish_bundle_download, Path(self.path).stem, delivered)


@router.get("/exports/bundles/{bundle_id}/status")
def export_bundle_status(bundle_id: Identifier, db: DB):
    archive_exists = export_bundle_path(bundle_id).is_file()
    bundle = db.get(RvcBundleSelection, bundle_id)
    cutoff = datetime.now(timezone.utc) - BUNDLE_SELECTION_TTL
    if bundle is None or bundle.created_at.replace(tzinfo=timezone.utc) < cutoff:
        return {"status": "expired"}
    if bundle.status == "pending" and not archive_exists:
        return {"status": "expired"}
    return {"status": bundle.status}


@router.get("/exports/bundles/{bundle_id}")
def download_export_bundle(bundle_id: Identifier, request: Request, db: DB):
    archive = export_bundle_path(bundle_id)
    if not archive.is_file() or archive.resolve() != archive:
        raise HTTPException(404, "合并下载已过期，请重新打包。")
    partial_request = "range" in request.headers
    tracked = not partial_request and db.get(RvcBundleSelection, bundle_id) is not None
    db.close()
    response = TrackedBundleDownload if tracked else FileResponse
    cleanup = None if tracked or partial_request else BackgroundTask(discard_export_bundle, archive)
    return response(archive, media_type="application/zip", filename="RVC-导出批次.zip",
                    headers={"Cache-Control": "no-store"}, background=cleanup)


@router.post("/history/remove")
def remove_history(data: HistoryRemoval, db: DB):
    exports = {(entry.project_id, entry.export_id) for entry in data.exports}
    with export_history_lock:
        jobs = db.execute(select(Job.id, Job.status).where(Job.id.in_(set(data.job_ids)))).all() if data.job_ids else []
        if any(status not in FINISHED_JOBS for _, status in jobs):
            raise HTTPException(409, "运行中的任务不能从历史记录中移除。")
        for project_id, export_id in exports:
            if find_export_archive(project_id, export_id) is None:
                raise HTTPException(404, "部分所选导出已不存在，请刷新历史记录。")
        for project_id, export_id in exports:
            db.merge(RemovedHistoryEntry(kind="export", project_id=project_id, item_id=export_id))
            selected = db.get(RvcExportSelection, (project_id, export_id))
            if selected is not None:
                db.delete(selected)
        for job_id, _ in jobs:
            db.merge(RemovedHistoryEntry(kind="job", project_id="", item_id=job_id))
        db.commit()
    return {"removed": len(exports) + len(jobs)}


@router.patch("/projects/{project_id}/exports/{export_id}")
def rename_export_batch(project_id: Identifier, export_id: Identifier, data: ExportNameInput, db: DB):
    with clip_file_lock, export_history_lock:
        get_project(db, project_id)
        removed = db.get(RemovedHistoryEntry, ("export", project_id, export_id))
        try:
            batch = None if removed else rename_export(project_id, export_id, data.batch_name)
        except FileExistsError as exc:
            raise HTTPException(409, "目标文件名已存在，请使用其他批次名称。") from exc
        except OSError as exc:
            raise HTTPException(409, "无法重命名导出批次。请关闭正在使用该文件夹或 ZIP 的程序后重试。") from exc
        if batch is None:
            raise HTTPException(404, "数据集导出不存在。")
        return batch


@router.get("/projects/{project_id}/exports/{export_id}")
def download_export(project_id: Identifier, export_id: Identifier, db: DB):
    get_project(db, project_id)
    with export_history_lock:
        archive = find_export_archive(project_id, export_id)
        if archive is None:
            raise HTTPException(404, "数据集导出不存在。")
        filename = f"rvc-{project_id[:8]}-{export_id[:8]}.zip" if archive.name == "dataset.zip" else archive.name
        return FileResponse(archive, media_type="application/zip", filename=filename)


def open_export_directory(folder: Path, *, create: bool = False) -> dict:
    if os.name != "nt":
        raise HTTPException(501, "此操作需要 Windows 资源管理器。")
    try:
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)
    except OSError as exc:
        raise HTTPException(500, "无法打开导出文件夹。") from exc
    return {"opened": True}


@router.post("/exports/open-folder")
def open_exports_folder():
    return open_export_directory(rvc_export_root(), create=True)


@router.post("/projects/{project_id}/exports/{export_id}/open-folder")
def open_batch_folder(project_id: Identifier, export_id: Identifier, db: DB):
    get_project(db, project_id)
    with export_history_lock:
        archive = find_export_archive(project_id, export_id)
        if archive is None:
            raise HTTPException(404, "数据集导出不存在。")
        return open_export_directory(archive.parent)
