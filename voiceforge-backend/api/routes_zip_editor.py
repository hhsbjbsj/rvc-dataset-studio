"""Inspect and edit uploaded ZIP files without unpacking their member paths."""

import mimetypes
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
import zlib
from contextlib import suppress
from copy import copy
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Lock
from typing import BinaryIO, Literal
from urllib.parse import unquote

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from api.routes_rvc import Identifier, pick_paths, require_local
from config import get_settings
from processing.rvc_audio import AUDIO_EXTENSIONS, MediaError, check_space
from rvc_projects import new_id

router = APIRouter(prefix="/api/rvc/zip-editor", tags=["ZIP editor"], dependencies=[Depends(require_local)])
CHUNK_SIZE = 1024 * 1024
SESSION_ID = re.compile(r"[0-9a-f]{32}")
SESSION_TTL = 24 * 60 * 60
ZIP_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, UnicodeError, EOFError, zlib.error)
overwrite_lock = Lock()


class EditZipInput(BaseModel):
    remove: list[int] = Field(min_length=1)


class PickZipInput(BaseModel):
    locale: Literal["zh-CN", "en", "ja"] = "zh-CN"


class LocalZipSource(BaseModel):
    path: Path
    fingerprint: tuple[int, int, int, int, int]


class ZipUndo(BaseModel):
    session_id: Identifier
    path: Path
    replacement: tuple[int, int, int, int]
    backup: tuple[int, int, int, int, int]


class ZipUndoState(BaseModel):
    previous: ZipUndo | None = None
    next: ZipUndo | None = None
    stage: Path | None = None

    def current(self) -> ZipUndo | None:
        # Atomic replacement consumes the staged file. This also resolves an interrupted save.
        return self.next if self.stage is not None and not self.stage.exists() else self.previous


def zip_editor_root() -> Path:
    return get_settings().TEMP_DIR.resolve() / "rvc-zip-editor"


def read_zip_undo() -> ZipUndoState:
    try:
        return ZipUndoState.model_validate_json((zip_editor_root() / "overwrite-undo.json").read_bytes())
    except FileNotFoundError:
        return ZipUndoState()


def write_zip_undo(state: ZipUndoState) -> None:
    root = zip_editor_root()
    stage = root / f"undo-{new_id()}.part"
    try:
        with stage.open("xb") as output:
            output.write(state.model_dump_json().encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, root / "overwrite-undo.json")
    finally:
        with suppress(OSError):
            stage.unlink(missing_ok=True)


def replace_zip(stage: Path, destination: Path, next_undo: ZipUndo | None) -> None:
    previous = read_zip_undo().current()
    write_zip_undo(ZipUndoState(previous=previous, next=next_undo, stage=stage))
    try:
        os.replace(stage, destination)
    except OSError:
        write_zip_undo(ZipUndoState(previous=previous))
        raise


def discard_staged_zip(stage: Path | None) -> None:
    if stage is not None:
        with suppress(OSError, ValidationError):
            # Keep a pending file if saving the rollback record failed.
            if read_zip_undo().stage != stage:
                stage.unlink(missing_ok=True)


def discard_zip_session(path: Path) -> None:
    if path.parent == zip_editor_root() and SESSION_ID.fullmatch(path.name) and path.resolve() == path:
        shutil.rmtree(path, ignore_errors=True)


def cleanup_zip_sessions() -> None:
    root = zip_editor_root()
    if root.resolve() != root:
        return
    cutoff = time.time() - SESSION_TTL
    with overwrite_lock:
        try:
            undo = read_zip_undo().current()
        except (OSError, ValidationError):
            return
        for session in root.glob("*"):
            try:
                if (undo is None or session.name != undo.session_id) and session.is_dir() and session.stat().st_mtime < cutoff:
                    discard_zip_session(session)
            except OSError:
                continue


def create_zip_session() -> Path:
    root = zip_editor_root()
    if root.resolve() != root:
        raise HTTPException(409, "无法使用 ZIP 编辑工作目录。")
    cleanup_zip_sessions()
    session = root / new_id()
    try:
        session.mkdir(parents=True)
    except OSError as exc:
        raise HTTPException(409, "无法使用 ZIP 编辑工作目录。") from exc
    return session


def open_session(session_id: str) -> Path:
    session = zip_editor_root() / session_id
    source = session / "original.zip"
    if session.resolve() != session or source.resolve() != source or not source.is_file():
        raise HTTPException(404, "ZIP 编辑会话已过期，请重新上传。")
    return session


def member_suffix(entry: zipfile.ZipInfo) -> str:
    return PurePosixPath(entry.orig_filename.replace("\\", "/")).suffix.lower()


def is_audio(entry: zipfile.ZipInfo) -> bool:
    return not entry.is_dir() and member_suffix(entry) in AUDIO_EXTENSIONS


def list_audio(path: Path) -> list[dict]:
    entries = []
    with zipfile.ZipFile(path) as archive:
        for index, entry in enumerate(archive.infolist()):
            name = entry.orig_filename.replace("\\", "/")
            member = PurePosixPath(name)
            if (not member.parts or member.is_absolute() or ":" in name
                    or any(not part.rstrip(" .") or PureWindowsPath(part).is_reserved() for part in member.parts)
                    or any(ord(char) < 32 for char in name) or stat.S_ISLNK(entry.external_attr >> 16)):
                raise MediaError("ZIP 内含不安全的路径或链接。")
            if entry.flag_bits & 1:
                raise MediaError("ZIP 内含加密文件，请先解密后再上传。")
            if is_audio(entry):
                entries.append({"id": index, "name": entry.orig_filename, "size": entry.file_size})
    return entries


def zip_session_data(session: Path, filename: str) -> dict:
    entries = list_audio(session / "original.zip")
    (session / "filename.txt").write_text(filename, encoding="utf-8")
    return {"id": session.name, "name": filename, "entries": entries}


def zip_fingerprint(path: Path) -> tuple[int, int, int, int, int]:
    if path.resolve(strict=True) != path:
        raise OSError("ZIP path changed")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise OSError("ZIP path is not a file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def require_unchanged(source: LocalZipSource) -> None:
    try:
        unchanged = zip_fingerprint(source.path) == source.fingerprint
    except OSError:
        unchanged = False
    if not unchanged:
        raise HTTPException(409, "原 ZIP 已变更，请重新打开后再覆盖。")


def require_undo_unchanged(undo: ZipUndo) -> None:
    try:
        unchanged = zip_fingerprint(undo.path)[:4] == undo.replacement
    except OSError:
        unchanged = False
    if not unchanged:
        raise HTTPException(409, "覆盖后的 ZIP 已变更，无法撤销。")


@router.post("/pick-local")
def pick_local_zip(data: PickZipInput):
    title, label = {
        "zh-CN": ("打开本机 ZIP", "ZIP 文件"),
        "en": ("Open a local ZIP", "ZIP files"),
        "ja": ("ローカル ZIP を開く", "ZIP ファイル"),
    }[data.locale]
    try:
        selected = pick_paths(title, filetypes=[(label, "*.zip")])
    except HTTPException as exc:
        if exc.status_code == 400:
            raise HTTPException(400, "无法打开本机 ZIP 选择对话框。") from exc
        raise
    if not selected:
        return None
    try:
        path = Path(selected[0])
        if not path.is_absolute():
            raise ValueError("Expected an absolute ZIP path")
        path = path.resolve(strict=True)
        if path.suffix.lower() != ".zip" or path.drive.startswith("\\\\"):
            raise ValueError("Expected a ZIP on a local disk")
        original = LocalZipSource(path=path, fingerprint=zip_fingerprint(path))
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "请选择本机磁盘上的 ZIP 文件。") from exc

    session = create_zip_session()
    completed = False
    try:
        check_space(session, path.stat().st_size)
        shutil.copyfile(path, session / "original.zip")
        require_unchanged(original)
        result = zip_session_data(session, path.name)
        (session / "source.json").write_text(original.model_dump_json(), encoding="utf-8")
        completed = True
        return {**result, "original_path": str(path)}
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ZIP_ERRORS as exc:
        raise HTTPException(400, "无法读取 ZIP，请检查文件是否损坏。") from exc
    except OSError as exc:
        raise HTTPException(409, "无法使用 ZIP 编辑工作目录。") from exc
    finally:
        if not completed:
            discard_zip_session(session)


@router.post("/uploads", status_code=201)
async def upload_zip(request: Request):
    filename = PureWindowsPath(unquote(request.headers.get("x-zip-name", ""))).name
    if not filename.lower().endswith(".zip") or any(ord(char) < 32 for char in filename):
        raise HTTPException(400, "请选择 ZIP 文件。")
    session = await run_in_threadpool(create_zip_session)
    source = session / "original.zip"
    completed = False
    try:
        expected = int(request.headers.get("content-length", "0"))
        await run_in_threadpool(check_space, session, expected)
        received = 0
        async with await anyio.open_file(source, "xb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if chunk and received > expected:
                    await run_in_threadpool(check_space, session, len(chunk))
                await output.write(chunk)
        result = await run_in_threadpool(zip_session_data, session, filename)
        completed = True
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ZIP_ERRORS as exc:
        raise HTTPException(400, "无法读取 ZIP，请检查文件是否损坏。") from exc
    except (ClientDisconnect, ValueError) as exc:
        raise HTTPException(400, "ZIP 上传未完成，请重新上传。") from exc
    except OSError as exc:
        raise HTTPException(409, "无法使用 ZIP 编辑工作目录。") from exc
    finally:
        if not completed:
            await run_in_threadpool(discard_zip_session, session)
    return result


def stream_audio(path: Path, index: int):
    with zipfile.ZipFile(path) as archive, archive.open(archive.infolist()[index]) as stream:
        while chunk := stream.read(CHUNK_SIZE):
            yield chunk


@router.get("/uploads/{session_id}/audio/{index}")
def preview_zip_audio(session_id: Identifier, index: int):
    source = open_session(session_id) / "original.zip"
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if not 0 <= index < len(entries) or not is_audio(entries[index]):
            raise HTTPException(404, "ZIP 内找不到这段音频。")
        entry = entries[index]
        media_type = mimetypes.guess_type(entry.filename)[0] or "application/octet-stream"
        return StreamingResponse(stream_audio(source, index), media_type=media_type, headers={
            "Content-Length": str(entry.file_size), "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        })


def write_edited_zip(source: Path, removed: set[int], output: BinaryIO) -> None:
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if any(not 0 <= index < len(entries) or not is_audio(entries[index]) for index in removed):
            raise HTTPException(400, "只能移除 ZIP 内的音频文件。")
        with zipfile.ZipFile(output, "w") as edited:
            edited.comment = archive.comment
            for index, entry in enumerate(entries):
                if index in removed:
                    continue
                saved_entry = copy(entry)
                saved_entry.filename = entry.orig_filename
                with archive.open(entry) as original, edited.open(saved_entry, "w") as member:
                    shutil.copyfileobj(original, member, CHUNK_SIZE)


@router.post("/uploads/{session_id}/edit")
def edit_zip(session_id: Identifier, data: EditZipInput):
    session = open_session(session_id)
    source = session / "original.zip"
    result_id = new_id()
    stage = session / f"{result_id}.part"
    target = session / f"{result_id}.zip"
    try:
        check_space(session, source.stat().st_size)
        with stage.open("xb") as output:
            write_edited_zip(source, set(data.remove), output)
        stage.rename(target)
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ZIP_ERRORS as exc:
        raise HTTPException(400, "无法读取 ZIP，请检查文件是否损坏。") from exc
    except OSError as exc:
        raise HTTPException(409, "无法写入编辑后的 ZIP，请检查工作盘。") from exc
    finally:
        stage.unlink(missing_ok=True)
    return {"download_url": f"/api/rvc/zip-editor/uploads/{session_id}/edited/{result_id}"}


def validate_zip_contents(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            with archive.open(entry) as stream:
                while stream.read(CHUNK_SIZE):
                    pass


@router.post("/uploads/{session_id}/overwrite", status_code=204)
def overwrite_original_zip(session_id: Identifier, data: EditZipInput):
    session = open_session(session_id)
    source = session / "original.zip"
    try:
        original = LocalZipSource.model_validate_json((session / "source.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(409, "请先用「打开本机 ZIP」选择原文件。") from exc
    except (OSError, ValidationError) as exc:
        raise HTTPException(409, "原 ZIP 已变更，请重新打开后再覆盖。") from exc
    require_unchanged(original)

    stage = None
    try:
        check_space(original.path.parent, source.stat().st_size)
        # An atomic replacement requires the temporary ZIP to be on the original volume.
        with tempfile.NamedTemporaryFile(dir=original.path.parent, prefix=".rvc-zip-", suffix=".tmp", delete=False) as output:
            stage = Path(output.name)
            write_edited_zip(source, set(data.remove), output)
            output.flush()
            os.fsync(output.fileno())
        validate_zip_contents(stage)
        with overwrite_lock:
            require_unchanged(original)
            replace_zip(stage, original.path, ZipUndo(
                session_id=session_id, path=original.path,
                replacement=zip_fingerprint(stage)[:4], backup=zip_fingerprint(source),
            ))
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ZIP_ERRORS as exc:
        raise HTTPException(400, "无法读取 ZIP，请检查文件是否损坏。") from exc
    except (OSError, ValidationError) as exc:
        raise HTTPException(409, "无法覆盖原 ZIP，请检查文件权限和磁盘空间。") from exc
    finally:
        discard_staged_zip(stage)
    return Response(status_code=204)


@router.get("/last-overwrite")
def last_zip_overwrite():
    with overwrite_lock:
        try:
            undo = read_zip_undo().current()
        except (OSError, ValidationError) as exc:
            raise HTTPException(409, "无法读取上次 ZIP 覆盖记录。") from exc
        if undo is None or not (zip_editor_root() / undo.session_id / "original.zip").is_file():
            return None
        return {"id": undo.session_id, "path": str(undo.path)}


@router.post("/last-overwrite/{session_id}/undo", status_code=204)
def undo_zip_overwrite(session_id: Identifier):
    stage = None
    try:
        with overwrite_lock:
            undo = read_zip_undo().current()
            if undo is None or undo.session_id != session_id:
                raise HTTPException(409, "上次 ZIP 覆盖记录已变更，请刷新后重试。")
            require_undo_unchanged(undo)
            backup = open_session(session_id) / "original.zip"
            if zip_fingerprint(backup) != undo.backup:
                raise HTTPException(409, "覆盖前的 ZIP 备份已变更，无法撤销。")
            check_space(undo.path.parent, backup.stat().st_size)
            with tempfile.NamedTemporaryFile(dir=undo.path.parent, prefix=".rvc-zip-", suffix=".tmp", delete=False) as output:
                stage = Path(output.name)
                with backup.open("rb") as source:
                    shutil.copyfileobj(source, output, CHUNK_SIZE)
                output.flush()
                os.fsync(output.fileno())
            if zip_fingerprint(backup) != undo.backup:
                raise HTTPException(409, "覆盖前的 ZIP 备份已变更，无法撤销。")
            require_undo_unchanged(undo)
            replace_zip(stage, undo.path, None)
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, ValidationError) as exc:
        raise HTTPException(409, "无法撤销 ZIP 覆盖，请检查文件权限和磁盘空间。") from exc
    finally:
        discard_staged_zip(stage)
    return Response(status_code=204)


@router.get("/uploads/{session_id}/edited/{result_id}")
def download_edited_zip(session_id: Identifier, result_id: Identifier):
    session = open_session(session_id)
    archive = session / f"{result_id}.zip"
    if archive.resolve() != archive or not archive.is_file():
        raise HTTPException(404, "编辑后的 ZIP 已过期，请重新打包。")
    filename = (session / "filename.txt").read_text(encoding="utf-8")
    return FileResponse(archive, media_type="application/zip", filename=filename, headers={"Cache-Control": "no-store"})
