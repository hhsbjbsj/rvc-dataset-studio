"""Export an immutable snapshot of the explicitly approved clips."""

import json
import logging
import os
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


def _batch_folder(root: Path, export_id: str, number: int) -> Path:
    return root / f"第{number:03d}批 [{export_id}]"


def _folder_id(folder: Path) -> str | None:
    match = BATCH_NAME.fullmatch(folder.name)
    return match[2] if match else folder.name if LEGACY_ID.fullmatch(folder.name) else None


def find_export_folder(project_id: str, export_id: str) -> Path | None:
    root = export_root(project_id)
    if not root.is_dir() or root.resolve() != root:
        return None
    for archive in root.glob("*/dataset.zip"):
        folder = archive.parent
        if _folder_id(folder) == export_id and folder.resolve() == folder and archive.is_file():
            return folder
    return None


def _named_project_root(project: dict) -> Path:
    root = export_root(project["id"])
    if root.name != project["id"]:
        return root
    character = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", project["character"]).strip(" .")[:36].rstrip(" .") or "角色"
    if PureWindowsPath(character).is_reserved():
        character = "_" + character
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


def _saved_exports(root: Path) -> list[SavedExport]:
    batches = []
    if root.resolve() != root:
        return batches
    for archive in root.glob("*/dataset.zip"):
        destination = archive.parent
        export_id = _folder_id(destination)
        if not export_id or destination.resolve() != destination or not archive.is_file():
            continue
        try:
            manifest = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
            summary = manifest["dataset"]
            if not isinstance(summary["created_at"], str) or not {"count", "duration_seconds", "sample_rate"} <= summary.keys():
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        match = BATCH_NAME.fullmatch(destination.name)
        batches.append(SavedExport(destination, export_id, summary, int(match[1]) if match else 0))
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
        _export_result(project_id, batch) for batch in reversed(_saved_exports(export_root(project_id)))
        if batch.export_id not in removed_ids
    ]


def migrate_export_folders(projects: list[dict]) -> None:
    for project in projects:
        if not _saved_exports(export_root(project["id"])):
            continue
        root = _named_project_root(project)
        for batch in _saved_exports(root):
            if batch.folder.name != batch.export_id:
                continue
            destination = _batch_folder(root, batch.export_id, batch.batch_number)
            if destination.exists():
                continue
            try:
                batch.folder.rename(destination)
            except OSError:
                logger.warning("Could not rename RVC export batch %s", batch.folder, exc_info=True)


def export_dataset(
    project: dict, entries: list[dict], sample_rate: int, *, scope: Literal["selected", "kept"],
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
    number = max((batch.batch_number for batch in _saved_exports(root)), default=0) + 1

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
            "count": len(metadata),
            "duration_seconds": round(seconds, 3),
            "sample_rate": sample_rate, "channels": 1, "codec": "pcm_s16le",
        }
        manifest = {
            "metadata.json": {"project": project, "dataset": summary, "clips": metadata},
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
        destination = _batch_folder(root, export_id, number)
        os.replace(stage, destination)

    return _export_result(project["id"], SavedExport(destination, export_id, summary, number))
