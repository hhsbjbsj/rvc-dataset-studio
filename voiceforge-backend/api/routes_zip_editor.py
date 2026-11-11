"""Inspect and edit uploaded ZIP files without unpacking their member paths."""

import mimetypes
import re
import shutil
import stat
import time
import zipfile
import zlib
from copy import copy
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from api.routes_rvc import Identifier, require_local
from config import get_settings
from processing.rvc_audio import AUDIO_EXTENSIONS, MediaError, check_space
from rvc_projects import new_id

router = APIRouter(prefix="/api/rvc/zip-editor", tags=["ZIP editor"], dependencies=[Depends(require_local)])
CHUNK_SIZE = 1024 * 1024
SESSION_ID = re.compile(r"[0-9a-f]{32}")
SESSION_TTL = 24 * 60 * 60
ZIP_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error)


class EditZipInput(BaseModel):
    remove: list[int] = Field(min_length=1)


def zip_editor_root() -> Path:
    return get_settings().TEMP_DIR.resolve() / "rvc-zip-editor"


def discard_zip_session(path: Path) -> None:
    if path.parent == zip_editor_root() and SESSION_ID.fullmatch(path.name) and path.resolve() == path:
        shutil.rmtree(path, ignore_errors=True)


def cleanup_zip_sessions() -> None:
    root = zip_editor_root()
    if root.resolve() != root:
        return
    cutoff = time.time() - SESSION_TTL
    for session in root.glob("*"):
        try:
            if session.is_dir() and session.stat().st_mtime < cutoff:
                discard_zip_session(session)
        except OSError:
            continue


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


@router.post("/uploads", status_code=201)
async def upload_zip(request: Request):
    filename = PureWindowsPath(unquote(request.headers.get("x-zip-name", ""))).name
    if not filename.lower().endswith(".zip") or any(ord(char) < 32 for char in filename):
        raise HTTPException(400, "请选择 ZIP 文件。")
    root = zip_editor_root()
    if root.resolve() != root:
        raise HTTPException(409, "无法使用 ZIP 编辑工作目录。")
    await run_in_threadpool(cleanup_zip_sessions)
    session_id = new_id()
    session = root / session_id
    source = session / "original.zip"
    created = False
    completed = False
    try:
        session.mkdir(parents=True)
        created = True
        expected = int(request.headers.get("content-length", "0"))
        await run_in_threadpool(check_space, session, expected)
        received = 0
        async with await anyio.open_file(source, "xb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if chunk and received > expected:
                    await run_in_threadpool(check_space, session, len(chunk))
                await output.write(chunk)
        entries = await run_in_threadpool(list_audio, source)
        (session / "filename.txt").write_text(filename, encoding="utf-8")
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
        if created and not completed:
            await run_in_threadpool(discard_zip_session, session)
    return {"id": session_id, "name": filename, "entries": entries}


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


@router.post("/uploads/{session_id}/edit")
def edit_zip(session_id: Identifier, data: EditZipInput):
    session = open_session(session_id)
    source = session / "original.zip"
    result_id = new_id()
    stage = session / f"{result_id}.part"
    target = session / f"{result_id}.zip"
    removed = set(data.remove)
    try:
        check_space(session, source.stat().st_size)
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if any(not 0 <= index < len(entries) or not is_audio(entries[index]) for index in removed):
                raise HTTPException(400, "只能移除 ZIP 内的音频文件。")
            with zipfile.ZipFile(stage, "x") as edited:
                edited.comment = archive.comment
                for index, entry in enumerate(entries):
                    if index in removed:
                        continue
                    saved_entry = copy(entry)
                    saved_entry.filename = entry.orig_filename
                    with archive.open(entry) as original, edited.open(saved_entry, "w") as output:
                        shutil.copyfileobj(original, output, CHUNK_SIZE)
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


@router.get("/uploads/{session_id}/edited/{result_id}")
def download_edited_zip(session_id: Identifier, result_id: Identifier):
    session = open_session(session_id)
    archive = session / f"{result_id}.zip"
    if archive.resolve() != archive or not archive.is_file():
        raise HTTPException(404, "编辑后的 ZIP 已过期，请重新打包。")
    filename = (session / "filename.txt").read_text(encoding="utf-8")
    return FileResponse(archive, media_type="application/zip", filename=filename, headers={"Cache-Control": "no-store"})
