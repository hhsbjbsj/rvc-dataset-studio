"""Export an immutable snapshot of the explicitly approved clips."""

import json
import logging
import re
import shutil
import tempfile
import zipfile
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Literal

from processing.rvc_audio import MediaError, WORK_RATE, check_space, ffmpeg, media_path
from rvc_projects import export_root, new_id

logger = logging.getLogger(__name__)
LEGACY_ID = re.compile(r"[0-9a-f]{32}")
BATCH_NAME = re.compile(r"第([0-9]{1,9})批 \[([0-9a-f]{32})\]")


@dataclass
class SavedExport:
    folder: Path
    export_id: str
    summary: dict
    batch_number: int = 0


def _legacy_batch_folder(root: Path, export_id: str, number: int) -> Path:
    return root / f"第{number:03d}批 [{export_id}]"


def _read_manifest(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _folder_id(folder: Path, project_id: str, manifest: dict | None) -> str | None:
    if manifest is not None and "export_id" in manifest:
        export_id = manifest["export_id"]
        project = manifest.get("project")
        if isinstance(export_id, str) and LEGACY_ID.fullmatch(export_id) and isinstance(project, dict) and project.get("id") == project_id:
            return export_id
        return None
    match = BATCH_NAME.fullmatch(folder.name)
    return match[2] if match else folder.name if LEGACY_ID.fullmatch(folder.name) else None


def find_export_folder(project_id: str, export_id: str) -> Path | None:
    root = export_root(project_id)
    if not root.is_dir() or root.resolve() != root:
        return None
    for archive in root.glob("*/dataset.zip"):
        folder = archive.parent
        if folder.resolve() == folder and archive.is_file() and _folder_id(folder, project_id, _read_manifest(folder)) == export_id:
            return folder
    return None


def _windows_folder_label(label: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")[:36].rstrip(" .")
    return "_" + safe if PureWindowsPath(safe).is_reserved() else safe


def _named_project_root(project: dict) -> Path:
    root = export_root(project["id"])
    if root.name != project["id"]:
        return root
    character = _windows_folder_label(project["character"]) or "角色"
    named = root.with_name(f"{character} [{project['id']}]")
    if root.is_dir() and root.resolve() == root and not named.exists():
        try:
            root.rename(named)
        except OSError:
            logger.warning("Could not rename RVC export directory %s", root, exc_info=True)
            return root
    return root if root.exists() else named


def _export_result(project_id: str, batch: SavedExport) -> dict:
    return {
        **batch.summary,
        "export_id": batch.export_id,
        "batch_number": batch.batch_number,
        "directory": str(batch.folder / "wavs"),
        "download_url": f"/api/rvc/projects/{project_id}/exports/{batch.export_id}",
    }


def _saved_exports(root: Path, project_id: str) -> list[SavedExport]:
    batches = []
    if root.resolve() != root:
        return batches
    for archive in root.glob("*/dataset.zip"):
        destination = archive.parent
        if destination.resolve() != destination or not archive.is_file():
            continue
        manifest = _read_manifest(destination)
        if manifest is None:
            continue
        export_id = _folder_id(destination, project_id, manifest)
        if export_id is None:
            continue
        try:
            summary = manifest["dataset"]
            if not isinstance(summary["created_at"], str) or not {"count", "duration_seconds", "sample_rate"} <= summary.keys():
                continue
        except (KeyError, TypeError):
            continue
        match = BATCH_NAME.fullmatch(destination.name)
        number = summary.get("batch_number")
        if type(number) is not int or number < 1:
            number = int(match[1]) if match else 0
        batches.append(SavedExport(destination, export_id, summary, number))
    batches.sort(key=lambda batch: (batch.summary["created_at"], batch.export_id))
    used = {batch.batch_number for batch in batches if batch.batch_number}
    for index, batch in enumerate(batches, 1):
        if not batch.batch_number:
            number = index
            while number in used:
                number += 1
            batch.batch_number = number
            used.add(number)
    return batches


def list_exports(project_id: str, removed_ids: Collection[str] = ()) -> list[dict]:
    return [
        _export_result(project_id, batch) for batch in reversed(_saved_exports(export_root(project_id), project_id))
        if batch.export_id not in removed_ids
    ]


def migrate_export_folders(projects: list[dict]) -> None:
    for project in projects:
        project_id = project["id"]
        if not _saved_exports(export_root(project_id), project_id):
            continue
        root = _named_project_root(project)
        for batch in _saved_exports(root, project_id):
            if batch.folder.name != batch.export_id:
                continue
            destination = _legacy_batch_folder(root, batch.export_id, batch.batch_number)
            if destination.exists():
                continue
            try:
                batch.folder.rename(destination)
            except OSError:
                logger.warning("Could not rename RVC export batch %s", batch.folder, exc_info=True)


def export_dataset(
    project: dict, entries: list[dict], sample_rate: int, *, scope: Literal["selected", "kept"],
    batch_name: str = "",
) -> dict:
    if not entries:
        raise MediaError("请先试听并保留需要导出的片段。")
    root = _named_project_root(project)
    if root.resolve() != root:
        raise MediaError("导出目录不是有效的本地项目目录。")
    root.mkdir(parents=True, exist_ok=True)
    seconds = sum(item["clip"]["duration"] for item in entries)
    check_space(root, int(seconds * sample_rate * 4))
    export_id = new_id()
    number = max((batch.batch_number for batch in _saved_exports(root, project["id"])), default=0) + 1
    label = _windows_folder_label(batch_name)
    suffix = f" - {label}" if label else ""
    destination = root / f"第{number:03d}批{suffix}"
    while destination.exists():
        number += 1
        destination = root / f"第{number:03d}批{suffix}"

    with tempfile.TemporaryDirectory(prefix="build-", dir=root) as temp:
        stage = Path(temp) / "dataset"
        wavs = stage / "wavs"
        wavs.mkdir(parents=True)
        metadata = []
        sources = {}
        for index, item in enumerate(entries, 1):
            source = item["source"]
            path = item["path"]
            if not path.is_file():
                raise MediaError("项目中的片段文件缺失，请检查工作目录。")
            name = f"{index:06d}.wav"
            output = wavs / name
            if sample_rate == WORK_RATE:
                shutil.copyfile(path, output)
            else:
                ffmpeg(["-protocol_whitelist", "file", "-i", media_path(path), "-ac", "1",
                        "-ar", str(sample_rate), "-c:a", "pcm_s16le", media_path(output)])
            sources[source["id"]] = source
            metadata.append({**item["clip"], "file": f"wavs/{name}", "processing": source["options"]})

        summary = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "batch_number": number,
            "count": len(metadata),
            "duration_seconds": round(seconds, 3),
            "sample_rate": sample_rate, "channels": 1, "codec": "pcm_s16le",
        }
        if batch_name:
            summary["batch_name"] = batch_name
        manifest = {
            "metadata.json": {"project": project, "export_id": export_id, "dataset": summary, "clips": metadata},
            "sources.json": list(sources.values()),
            "quality_report.json": {
                **summary,
                "mean_score": round(sum(clip["quality"]["score"] for clip in metadata) / len(metadata), 1),
                "clips": [{"file": clip["file"], **clip["quality"]} for clip in metadata],
            },
        }
        for name, value in manifest.items():
            (stage / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        with zipfile.ZipFile(stage / "dataset.zip", "w", compression=zipfile.ZIP_STORED) as archive:
            for path in [*wavs.iterdir(), *(stage / name for name in manifest)]:
                archive.write(path, path.relative_to(stage).as_posix())
        stage.rename(destination)

    return _export_result(project["id"], SavedExport(destination, export_id, summary, number))
