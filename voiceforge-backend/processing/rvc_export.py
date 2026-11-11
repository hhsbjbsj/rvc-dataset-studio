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
LEGACY_BATCH = re.compile(r"第([0-9]{1,9})批 \[([0-9a-f]{32})\]")
NAMED_BATCH = re.compile(r"第([0-9]{1,9})批(?: - .+)?")


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
    match = LEGACY_BATCH.fullmatch(folder.name)
    return match[2] if match else folder.name if LEGACY_ID.fullmatch(folder.name) else None


def _batch_folders(root: Path):
    if not root.is_dir() or root.resolve() != root:
        return
    for folder in root.iterdir():
        if folder.is_dir() and folder.resolve() == folder:
            yield folder


def _export_archive(folder: Path) -> Path | None:
    for name in (f"{folder.name}.zip", "dataset.zip"):
        archive = folder / name
        if archive.is_file() and archive.resolve() == archive:
            return archive
    # An interrupted rename can leave the ZIP under its new name in the old folder.
    candidates = [path for path in folder.glob("第*批*.zip")
                  if NAMED_BATCH.fullmatch(path.stem) and path.is_file() and path.resolve() == path]
    return candidates[0] if len(candidates) == 1 else None


def find_export_archive(project_id: str, export_id: str) -> Path | None:
    for folder in _batch_folders(export_root(project_id)):
        if _folder_id(folder, project_id, _read_manifest(folder)) == export_id:
            return _export_archive(folder)
    return None


def _windows_folder_label(label: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")[:36].rstrip(" .")
    return "_" + safe if PureWindowsPath(safe).is_reserved() else safe


def _batch_folder_name(number: int, batch_name: str) -> str:
    label = _windows_folder_label(batch_name)
    return f"第{number:03d}批 - {label}" if label else f"第{number:03d}批"


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
    for destination in _batch_folders(root):
        manifest = _read_manifest(destination)
        if manifest is None:
            continue
        export_id = _folder_id(destination, project_id, manifest)
        if export_id is None or _export_archive(destination) is None:
            continue
        try:
            summary = manifest["dataset"]
            if not isinstance(summary["created_at"], str) or not {"count", "duration_seconds", "sample_rate"} <= summary.keys():
                continue
        except (KeyError, TypeError):
            continue
        match = LEGACY_BATCH.fullmatch(destination.name) or NAMED_BATCH.fullmatch(destination.name)
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


def rename_export(project_id: str, export_id: str, batch_name: str) -> dict | None:
    batch = next((entry for entry in _saved_exports(export_root(project_id), project_id)
                  if entry.export_id == export_id), None)
    if batch is None:
        return None
    folder = batch.folder
    manifest = _read_manifest(folder)
    archive = _export_archive(folder)
    if manifest is None or archive is None:
        return None

    destination = folder.with_name(_batch_folder_name(batch.batch_number, batch_name))
    named_archive = archive.with_name(f"{destination.name}.zip")
    for original, target in ((folder, destination), (archive, named_archive)):
        if target != original and (target.exists() or target.is_symlink()):
            raise FileExistsError(target)

    metadata = folder / "metadata.json"
    if metadata.is_symlink() or metadata.resolve() != metadata:
        raise OSError("Export metadata is not a local file")
    with tempfile.TemporaryDirectory(prefix="rename-", dir=folder.parent) as temp:
        pending = Path(temp) / "metadata.json"
        if "export_id" not in manifest:
            # Older exports identified the batch by its directory name alone.
            project = manifest.get("project")
            manifest["project"] = {**(project if isinstance(project, dict) else {}), "id": project_id}
            manifest["export_id"] = export_id
            pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            pending.replace(metadata)

        summary = manifest["dataset"]
        summary["batch_number"] = batch.batch_number
        if batch_name:
            summary["batch_name"] = batch_name
        else:
            summary.pop("batch_name", None)
        pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        current_folder = folder
        current_archive = archive.name
        try:
            if archive.name != named_archive.name:
                archive.rename(named_archive)
                current_archive = named_archive.name
            if folder.name != destination.name:
                folder.rename(destination)
                current_folder = destination
            pending.replace(current_folder / metadata.name)
        except OSError:
            try:
                if current_folder.name != folder.name:
                    current_folder.rename(folder)
                if current_archive != archive.name:
                    archive.with_name(current_archive).rename(archive)
            except OSError:
                logger.exception("Could not restore RVC export batch names for %s", export_id)
            raise

    return _export_result(project_id, SavedExport(destination, export_id, summary, batch.batch_number))


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
    destination = root / _batch_folder_name(number, batch_name)
    while destination.exists():
        number += 1
        destination = root / _batch_folder_name(number, batch_name)

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
        with zipfile.ZipFile(stage / f"{destination.name}.zip", "w", compression=zipfile.ZIP_STORED) as archive:
            for path in [*wavs.iterdir(), *(stage / name for name in manifest)]:
                archive.write(path, path.relative_to(stage).as_posix())
        stage.rename(destination)

    return _export_result(project["id"], SavedExport(destination, export_id, summary, number))
