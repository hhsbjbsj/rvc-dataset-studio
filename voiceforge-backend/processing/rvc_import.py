"""Keep a project-owned copy of a converter batch."""

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from processing.rvc_audio import MediaError, check_space


def stage_conversion(archive_path: Path, destination: Path) -> list[tuple[Path, str]]:
    with zipfile.ZipFile(archive_path) as archive:
        entries = [
            entry for entry in archive.infolist()
            if not entry.is_dir() and Path(entry.filename).suffix.lower() in {".wav", ".flac"}
        ]
        if not entries:
            raise MediaError("此转换结果没有可导入的音频文件。")
        files = [
            (destination / f"{index:06d}{Path(entry.filename).suffix.lower()}", Path(entry.filename).name)
            for index, entry in enumerate(entries, 1)
        ]
        if not destination.is_dir():
            root = destination.parent
            root.mkdir(parents=True, exist_ok=True)
            check_space(root, sum(entry.file_size for entry in entries))
            with tempfile.TemporaryDirectory(prefix="import-", dir=root) as temp:
                stage = Path(temp) / "audio"
                stage.mkdir()
                for entry, (path, _) in zip(entries, files, strict=True):
                    with archive.open(entry) as source, (stage / path.name).open("wb") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                os.replace(stage, destination)
        return files
