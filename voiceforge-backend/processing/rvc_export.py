"""Export an immutable snapshot of the explicitly approved clips."""

import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from processing.rvc_audio import MediaError, WORK_RATE, check_space, ffmpeg, media_path
from rvc_projects import export_root, new_id


def _export_result(destination: Path, summary: dict) -> dict:
    project_id = destination.parent.name
    export_id = destination.name
    return {
        **summary,
        "export_id": export_id,
        "directory": str(destination / "wavs"),
        "download_url": f"/api/rvc/projects/{project_id}/exports/{export_id}",
    }


def list_exports(project_id: str) -> list[dict]:
    batches = []
    for archive in export_root(project_id).glob("*/dataset.zip"):
        destination = archive.parent
        if not re.fullmatch(r"[0-9a-f]{32}", destination.name) or not archive.is_file():
            continue
        try:
            manifest = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
            summary = manifest["dataset"]
            if not isinstance(summary["created_at"], str) or not {"count", "duration_seconds", "sample_rate"} <= summary.keys():
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        batches.append(_export_result(destination, summary))
    return sorted(batches, key=lambda batch: (batch["created_at"], batch["export_id"]), reverse=True)


def export_dataset(
    project: dict, entries: list[dict], sample_rate: int, *, scope: Literal["selected", "kept"],
) -> dict:
    if not entries:
        raise MediaError("请先试听并保留需要导出的片段。")
    root = export_root(project["id"])
    root.mkdir(parents=True, exist_ok=True)
    seconds = sum(item["clip"]["duration"] for item in entries)
    check_space(root, int(seconds * sample_rate * 4))
    export_id = new_id()

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
        destination = root / export_id
        os.replace(stage, destination)

    return _export_result(destination, summary)
