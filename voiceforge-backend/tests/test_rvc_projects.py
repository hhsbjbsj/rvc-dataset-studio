"""Windows FFmpeg integration and offline checks for the mixed-source workflow."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import time
import wave
import zipfile
from pathlib import Path

import httpx
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from config import rvc_export_root
from database import Job, SessionLocal
from main import app
from processing.rvc_audio import MediaError, write_clip
from rvc_projects import RvcClip, clip_path, export_root, new_id, work_dir
from sources.rvc_online import normalize_url

VIDEO = "https://www.bilibili.com/video/BV1n44y1Q7sc"


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1") as connection:
        yield connection


def new_project(client):
    response = client.post("/api/rvc/projects", json={
        "series": "测试作品", "character": "测试角色", "language": "中文", "target_minutes": 5,
    })
    assert response.status_code == 201, response.text
    return "/api/rvc/projects/" + response.json()["id"]


def post(client, url, data):
    response = client.post(url, json=data)
    assert response.status_code < 300, response.text
    return response.json()


def test_workspace_remembers_explicitly_opened_project(client):
    workspace = "/api/rvc/workspace"
    before = client.get(workspace).json()
    older = new_project(client).rsplit("/", 1)[-1]
    newer = new_project(client).rsplit("/", 1)[-1]
    assert client.get(workspace).json() == before
    assert client.get("/api/rvc/projects").json()[0]["id"] == newer
    assert client.put(workspace, json={"project_id": older}).json() == {"last_project_id": older}
    assert client.put(workspace, json={"project_id": "0" * 32}).status_code == 404
    assert client.get(workspace).json() == {"last_project_id": older}
    assert client.put(workspace, json={"project_id": newer}).json() == {"last_project_id": newer}


@pytest.mark.skipif(os.name != "nt", reason="Windows File Explorer integration")
def test_open_export_folder_uses_the_configured_location(client, monkeypatch):
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append)
    base = new_project(client)
    project_id = base.rsplit("/", 1)[-1]
    export_id = new_id()
    root = rvc_export_root().resolve()
    batch = root / project_id / export_id
    batch.mkdir(parents=True)
    (batch / "dataset.zip").write_bytes(b"export")
    folder_url = base + "/exports/" + export_id + "/open-folder"
    assert client.post(folder_url).json() == {"opened": True}
    assert client.post("/api/rvc/exports/open-folder").json() == {"opened": True}
    assert client.post(folder_url, headers={"Origin": "https://example.com"}).status_code == 403
    assert client.post(base + "/exports/" + "0" * 32 + "/open-folder").status_code == 404
    assert opened == [batch, root]


def wait_for_sources(client, base):
    for _ in range(150):
        sources = client.get(base).json()["sources"]
        if all(source["status"] in {"done", "failed", "cancelled"} for source in sources):
            return sources
        time.sleep(0.1)
    pytest.fail(f"Queue did not settle: {sources}")


def ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_threads", "1", *map(str, args)],
        check=True, capture_output=True,
    )


@pytest.fixture(scope="module")
def speech(tmp_path_factory):
    if os.name != "nt" or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Windows System.Speech, FFmpeg and FFprobe are needed for these media fixtures")
    folder = tmp_path_factory.mktemp("本地 素材")
    statements = [
        "We are preparing a voice dataset. Listen carefully to the first sentence.",
        "The second recording has a different line of dialogue for testing.",
        "A third speaker sample demonstrates the end of the source recording.",
    ]
    script = """
        $ErrorActionPreference = 'Stop'
        Add-Type -AssemblyName System.Speech
        $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
        try {
            $synth.Rate = -1
            $lines = ConvertFrom-Json $env:RVC_SPEECH_TEXT
            for ($i = 0; $i -lt $lines.Count; $i++) {
                $path = Join-Path $env:RVC_SPEECH_DIR ("line-" + $i + ".wav")
                $synth.SetOutputToWaveFile($path)
                $synth.Speak($lines[$i])
                $synth.SetOutputToNull()
            }
        } finally {
            $synth.Dispose()
        }
    """
    paths = [folder / f"line-{index}.wav" for index in range(len(statements))]
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "RVC_SPEECH_DIR": str(folder), "RVC_SPEECH_TEXT": json.dumps(statements)},
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    long_wav = folder / "长视频 配音.wav"
    with wave.open(str(long_wav), "wb") as combined:
        with wave.open(str(paths[0]), "rb") as line:
            combined.setnchannels(line.getnchannels())
            combined.setsampwidth(line.getsampwidth())
            combined.setframerate(line.getframerate())
        silence = bytes(combined.getframerate() * combined.getsampwidth() * combined.getnchannels())
        for _ in range(4):
            for path in paths:
                with wave.open(str(path), "rb") as line:
                    combined.writeframesraw(line.readframes(line.getnframes()))
                combined.writeframesraw(silence)
    return folder, long_wav, paths


def test_bilibili_search_and_url_validation(client, monkeypatch):
    def response(url, **options):
        assert url == "https://api.bilibili.com/x/web-interface/search/type"
        assert options["params"]["page"] == 2
        entry = {"bvid": "BV1n44y1Q7sc", "title": "<em>游戏</em>&amp;台词", "author": "测试UP", "duration": "1:05"}
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "code": 0, "data": {"numPages": 3, "result": [entry, entry]},
        })

    monkeypatch.setattr("sources.rvc_online.httpx.get", response)
    result = client.get("/api/rvc/search/bilibili", params={"q": "语音", "page": 2})
    assert result.status_code == 200
    assert result.json() == {"videos": [{
        "url": VIDEO, "platform": "bilibili", "title": "游戏&台词", "uploader": "测试UP", "duration": 65,
    }], "has_more": True}
    assert normalize_url(VIDEO + "?p=2&spm_id_from=test") == ("bilibili", VIDEO + "?p=2")
    with pytest.raises(ValueError):
        normalize_url("https://www.bilibili.com@127.0.0.1/video/BV1n44y1Q7sc")
    assert client.get("/api/rvc/projects", headers={"host": "example.com"}).status_code == 403
    assert client.post("/api/rvc/projects", json={}, headers={"origin": "https://example.com"}).status_code == 403


def test_bilibili_short_link_import_preserves_part_and_checks_target(client, monkeypatch):
    short = "https://b23.tv/AbCd123"
    other = "https://b23.tv/Redirect"
    destinations = {short: VIDEO + "?p=2&share_source=test", other: "http://127.0.0.1/video/"}
    requested = []

    def redirect(url, *, follow_redirects, timeout):
        assert not follow_redirects and timeout > 0
        requested.append(url)
        return httpx.Response(302, headers={"Location": destinations[url]})

    monkeypatch.setattr("sources.rvc_online.httpx.get", redirect)
    base = new_project(client)
    result = post(client, base + "/sources/online", {"videos": [
        {"url": short, "title": "B 站分 P"},
        {"url": VIDEO + "?p=2"},
        {"url": other},
    ]})
    assert result["added"] == 1
    assert result["duplicates"] == 1
    assert [error["origin"] for error in result["errors"]] == [other]
    assert requested == [short, other]
    assert client.get(base).json()["sources"][0]["origin"] == VIDEO + "?p=2"


def test_windows_mixed_media_review_and_export(client, speech, monkeypatch):
    folder, long_wav, lines = speech
    video_folder = folder / "视频"
    video_folder.mkdir()
    nested = video_folder / "子目录"
    nested.mkdir()
    mp4 = video_folder / "剧情 视频.mp4"
    mkv = nested / "语音 合集.mkv"
    copy = nested / "同一 视频.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=160x90:r=2",
        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-i", long_wav,
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-shortest",
        "-c:v", "mpeg4", "-threads:v", "1", "-c:a", "aac", "-metadata:s:a:1", "language=eng", mp4,
    )
    ffmpeg("-i", mp4, "-map", "0:v:0", "-map", "0:a:1", "-c", "copy", mkv)
    shutil.copyfile(mp4, copy)
    mp3 = folder / "来源一.mp3"
    flac = folder / "来源二.flac"
    ffmpeg("-i", lines[0], mp3)
    ffmpeg("-i", lines[1], flac)
    hashes = {file: hashlib.sha256(file.read_bytes()).hexdigest() for file in (mp4, mkv, copy, mp3, flac, lines[2])}
    base = new_project(client)

    result = post(client, base + "/sources/local", {"kind": "local_video", "paths": [str(video_folder)], "recursive": True})
    assert result == {"added": 3, "duplicates": 0, "errors": []}
    sources = client.get(base).json()["sources"]
    for source in sources:
        if len(source["media"]["tracks"]) == 2:
            index = next(track["index"] for track in source["media"]["tracks"] if track["language"] == "eng")
            response = client.patch(base + "/sources/" + source["id"] + "/track", json={"index": index})
            assert response.status_code == 200, response.text
    assert post(client, base + "/sources/local", {
        "kind": "local_video", "paths": [str(mp4)],
    })["duplicates"] == 1
    assert post(client, base + "/sources/local", {
        "kind": "local_audio", "paths": [str(mp3), str(flac), str(lines[2])],
    })["added"] == 3
    assert post(client, base + "/sources/online", {
        "videos": [{"url": VIDEO}, {"url": VIDEO + "?spm_id_from=abc"}],
    }) == {"added": 1, "duplicates": 1, "errors": []}

    def fake_download(url, work, check, progress):
        assert url == VIDEO
        check()
        progress(38)
        result = work / "network-fixture.mp3"
        ffmpeg("-i", lines[2], result)
        return result

    monkeypatch.setattr("workers.rvc_worker.download", fake_download)
    assert post(client, base + "/process", {"min_seconds": 0.45, "max_seconds": 8}) == {"queued": 7}
    completed = wait_for_sources(client, base)
    assert all(source["status"] == "done" for source in completed), [
        (source["title"], source["error"]) for source in completed if source["status"] != "done"
    ]
    assert next(item for item in completed if item["title"] == copy.name)["duplicate_clips"] > 0
    clips = client.get(base + "/clips").json()["clips"]
    assert len(clips) >= 5
    assert {clip["source_kind"] for clip in clips} == {"local_video", "local_audio", "bilibili"}
    assert all(0 <= clip["start"] < clip["end"] and clip["duration"] <= 8.01 for clip in clips)
    preview = client.get(base + "/clips/" + clips[0]["id"] + "/audio")
    assert preview.status_code == 200 and preview.content.startswith(b"RIFF")
    assert client.post(base + "/export", json={}).status_code == 400
    assert client.post(base + "/export", json={"clip_ids": []}).status_code == 422
    assert client.post(base + "/export", json={"clip_ids": [clips[0]["id"], "0" * 32]}).status_code == 400

    batches = []
    filenames = set()
    for clip in (clips[0], clips[-1]):
        selected_output = post(client, base + "/export", {"clip_ids": [clip["id"]], "sample_rate": 40000})
        assert selected_output["count"] == 1
        download = client.get(selected_output["download_url"])
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as bundle:
            metadata = json.loads(bundle.read("metadata.json"))
            assert [item["id"] for item in metadata["clips"]] == [clip["id"]]
            assert metadata["dataset"]["scope"] == "selected"
            assert [name for name in bundle.namelist() if name.endswith(".wav")] == ["wavs/000001.wav"]
        batches.append(selected_output)
        filenames.add(download.headers["content-disposition"])
    assert len(filenames) == 2
    assert client.get(base).json()["review"]["pending"]["count"] == len(clips)
    assert client.post(base + "/export", json={}).status_code == 400

    retained = clips[:-1]
    assert client.patch(base + "/clips/review", json={
        "ids": [clip["id"] for clip in retained], "review": "kept",
    }).json()["updated"] == len(retained)
    assert client.patch(base + "/clips/review", json={"ids": [clips[-1]["id"]], "review": "excluded"}).status_code == 200

    output = post(client, base + "/export", {"sample_rate": 40000})
    assert output["scope"] == "kept"
    assert client.get(base + "/exports").json() == [
        {**batch, "batch_number": number} for number, batch in zip((3, 2, 1), (output, *reversed(batches)))
    ]
    other_project = new_project(client)
    assert client.get(other_project + "/exports").json() == []
    assert client.get(other_project + "/exports/" + output["export_id"]).status_code == 404

    legacy_path = Path(batches[0]["directory"]).parent / "metadata.json"
    legacy_metadata = json.loads(legacy_path.read_text(encoding="utf-8"))
    del legacy_metadata["dataset"]["scope"]
    legacy_path.write_text(json.dumps(legacy_metadata), encoding="utf-8")
    del batches[0]["scope"]
    expected_batches = [
        {**batch, "batch_number": number} for number, batch in zip((3, 2, 1), (output, *reversed(batches)))
    ]
    assert client.get(base + "/exports").json() == expected_batches
    history = client.get("/api/rvc/exports").json()
    history_by_project = {group["project"]["id"]: group for group in history}
    assert other_project.rsplit("/", 1)[-1] not in history_by_project
    project_history = history_by_project[base.rsplit("/", 1)[-1]]
    assert project_history["project"]["character"] == "测试角色"
    assert project_history["batches"] == expected_batches
    assert client.get(batches[0]["download_url"]).status_code == 200

    wavs = Path(output["directory"])
    assert len(list(wavs.glob("*.wav"))) == len(retained) == output["count"]
    assert all(hashlib.sha256(file.read_bytes()).hexdigest() == digest for file, digest in hashes.items())
    archive = client.get(output["download_url"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert {"metadata.json", "sources.json", "quality_report.json"} <= set(bundle.namelist())
        with wave.open(io.BytesIO(bundle.read("wavs/000001.wav"))) as first:
            assert (first.getnchannels(), first.getsampwidth(), first.getframerate()) == (1, 2, 40000)


def test_remove_selected_history_preserves_files_and_batch_numbers(client):
    base = new_project(client)
    project_id = base.rsplit("/", 1)[-1]
    export_ids = [new_id() for _ in range(3)]
    for index, export_id in enumerate(export_ids, 1):
        destination = export_root(project_id) / export_id
        (destination / "wavs").mkdir(parents=True)
        (destination / "wavs" / "sample.wav").write_bytes(b"audio")
        with zipfile.ZipFile(destination / "dataset.zip", "w") as archive:
            archive.write(destination / "wavs" / "sample.wav", "wavs/sample.wav")
        (destination / "metadata.json").write_text(json.dumps({"dataset": {
            "created_at": f"2026-01-0{index}T00:00:00+00:00", "count": 1, "duration_seconds": 1, "sample_rate": 40000,
        }}), encoding="utf-8")

    removed_job, kept_job, active_job = [new_id() for _ in range(3)]
    with SessionLocal.begin() as db:
        db.add_all([Job(id=job_id, query="Test conversion", query_type="conversion", status=status)
                    for job_id, status in ((removed_job, "done"), (kept_job, "done"), (active_job, "processing"))])
    remove_url = "/api/rvc/history/remove"
    oldest = export_root(project_id) / export_ids[0]
    download_url = base + "/exports/" + export_ids[0]
    original_zip = client.get(download_url).content
    selection = {"exports": [{"project_id": project_id, "export_id": export_ids[0]}], "job_ids": [removed_job]}
    assert client.post(remove_url, json={**selection, "job_ids": [active_job]}).status_code == 409
    assert len(client.get(base + "/exports").json()) == 3
    assert post(client, remove_url, selection) == {"removed": 2}
    assert post(client, remove_url, selection) == {"removed": 2}
    remaining = client.get(base + "/exports").json()
    assert [(row["export_id"], row["batch_number"]) for row in remaining] == [(export_ids[2], 3), (export_ids[1], 2)]
    global_history = client.get("/api/rvc/exports").json()
    assert next(group for group in global_history if group["project"]["id"] == project_id)["batches"] == remaining
    assert client.get(download_url).content == original_zip
    assert (oldest / "wavs" / "sample.wav").read_bytes() == b"audio"
    visible_jobs = {job["id"] for job in client.get("/api/history").json()}
    assert removed_job not in visible_jobs and {kept_job, active_job} <= visible_jobs
    assert client.get(f"/api/jobs/{removed_job}").status_code == 200


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Requires FFmpeg and FFprobe")
def test_convert_mp3_archives_into_rvc_project(client, tmp_path):
    uploads = []
    for name, frequency, gain in (("_take", 440, 0.3), ("line", 550, 1.5)):
        mp3 = tmp_path / (name + ".mp3")
        ffmpeg("-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.5", "-af", f"volume={gain}", mp3)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.write(mp3, "dataset/" + mp3.name)
        uploads.append(("files", (name + ".zip", archive.getvalue(), "application/zip")))

    response = client.post("/api/convert", files=uploads, data={"sample_rate": "22050", "normalize": "true"})
    assert response.status_code == 200, response.text
    converted = response.json()
    assert converted["files_converted"] == 2 and converted["errors"] == []
    download = client.get(converted["download_url"])
    with zipfile.ZipFile(io.BytesIO(download.content)) as bundle:
        assert set(bundle.namelist()) == {"_take.wav", "line.wav"}
        for name in bundle.namelist():
            samples, sample_rate = sf.read(io.BytesIO(bundle.read(name)))
            assert samples.ndim == 1 and sample_rate == 22050
            assert (samples ** 2).mean() ** 0.5 == pytest.approx(0.1, abs=0.005)

    project = new_project(client)
    other_project = new_project(client)
    transfer = project + "/sources/converted/" + converted["job_id"]
    assert post(client, transfer, {}) == {"queued": 2, "duplicates": 0}
    assert post(client, transfer, {}) == {"queued": 0, "duplicates": 2}
    assert client.get(other_project).json()["sources"] == []
    assert client.delete("/api/jobs/" + converted["job_id"]).status_code == 200

    sources = wait_for_sources(client, project)
    assert {source["title"] for source in sources} == {"_take.wav", "line.wav"}
    assert all(source["status"] == "done" and Path(source["origin"]).is_file() for source in sources)
    assert all(not source["options"]["split"] and not source["options"]["normalize"] for source in sources)
    clips = client.get(project + "/clips").json()["clips"]
    assert len(clips) == 2 and all(clip["review"] == "pending" for clip in clips)
    for clip in clips:
        assert clip["duration"] == pytest.approx(0.5, abs=0.02)
        assert client.get(project + "/clips/" + clip["id"] + "/audio").content.startswith(b"RIFF")


def test_manual_edit_uses_the_source_timeline(client, speech, monkeypatch):
    _, source_file, _ = speech
    base = new_project(client)
    assert post(client, base + "/sources/local", {"kind": "local_audio", "paths": [str(source_file)]})["added"] == 1
    source = client.get(base).json()["sources"][0]
    source_base = base + "/sources/" + source["id"]
    assert client.post(source_base + "/prepare-audio").status_code == 409
    post(client, base + "/process", {"min_seconds": 0.45})
    assert wait_for_sources(client, base)[0]["status"] == "done"

    info = post(client, source_base + "/prepare-audio", {})
    assert info["duration"] > 8 and info["offset"] == 0
    ranged = client.get(source_base + "/original-audio", headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206 and ranged.content.startswith(b"fLaC")
    source_samples, rate = sf.read(io.BytesIO(client.get(source_base + "/original-audio").content), dtype="int16")
    assert rate == 48000
    source_waveform = client.get(source_base + "/waveform")
    assert source_waveform.status_code == 200
    peaks = source_waveform.json()["peaks"]
    assert len(peaks) == 8192
    first_bin = source_samples[:len(source_samples) // len(peaks)]
    assert peaks[0] == round(max(abs(int(sample)) for sample in first_bin) / 32768, 5)

    clips = client.get(base + "/clips").json()["clips"]
    first, second, third = clips[:3]
    assert first["end"] < second["start"]
    merge_url = base + "/clips/merge"
    selected = {"clip_ids": [second["id"], first["id"]]}

    def clip_audio(clip):
        return client.get(base + "/clips/" + clip["id"] + "/audio")

    with SessionLocal() as db:
        old_paths = [clip_path(db.get(RvcClip, item["id"])) for item in (first, second)]

    def fail_write(raw, path, *args, **kwargs):
        path.write_bytes(b"incomplete")
        raise MediaError("模拟写入失败")

    with monkeypatch.context() as patch:
        patch.setattr("processing.rvc_edit.write_clip", fail_write)
        assert client.post(merge_url, json=selected).status_code == 400
    assert all(path.is_file() for path in old_paths)
    assert client.get(base + "/clips").json()["total"] == len(clips)

    merged = post(client, merge_url, selected)
    assert (merged["start"], merged["end"]) == (first["start"], second["end"])
    assert merged["review"] == "pending" and merged["quality"]["edit"] == "merge"
    assert merged["replaced_count"] == 2
    assert client.get(base + "/clips").json()["total"] == len(clips) - 1
    buffer_url = base + "/merge-buffer"
    assert {row["id"] for row in client.get(buffer_url).json()["clips"]} == set(selected["clip_ids"])
    assert clip_audio(first).status_code == clip_audio(second).status_code == 200
    assert all(path.exists() for path in old_paths)
    assert client.post(base + "/export", json=selected).status_code == 400
    assert client.delete(buffer_url + "/" + merged["id"]).status_code == 404

    assert client.delete(buffer_url + "/" + first["id"]).status_code == 200
    assert clip_audio(first).status_code == 404
    assert not old_paths[0].exists()
    assert post(client, buffer_url + "/" + second["id"] + "/restore", {})["id"] == second["id"]
    assert old_paths[1].is_file()
    assert client.get(buffer_url).json()["clips"] == []
    assert second["id"] in {row["id"] for row in client.get(base + "/clips").json()["clips"]}

    with wave.open(io.BytesIO(clip_audio(merged).content)) as wav:
        begin, end = (round(merged[key] * rate) for key in ("start", "end"))
        assert wav.readframes(wav.getnframes()) == source_samples[begin:end].astype("<i2").tobytes()
    assert client.post(merge_url, json=selected).status_code == 400

    waveform = client.get(base + "/clips/" + merged["id"] + "/waveform")
    assert waveform.status_code == 200 and max(waveform.json()["peaks"]) > 0
    assert client.get(base + "/clips/" + first["id"] + "/waveform").status_code == 404

    trim_url = base + "/clips/" + merged["id"] + "/trim"
    assert client.post(trim_url, json={"start": 0, "end": merged["duration"]}).status_code == 400
    assert client.post(trim_url, json={"start": 0.2, "end": merged["duration"] + 1}).status_code == 400

    def unavailable_source(*args):
        raise AssertionError("Trimming a clip must not read the original source")

    with monkeypatch.context() as patch:
        patch.setattr("processing.rvc_edit.prepare_source_audio", unavailable_source)
        trimmed_clip = post(client, trim_url, {"start": 0.25, "end": 0.65})
        assert post(client, trim_url, {"start": 0.25, "end": 0.65})["id"] == trimmed_clip["id"]
    assert trimmed_clip["id"] != merged["id"] and trimmed_clip["source_id"] == merged["source_id"]
    assert trimmed_clip["quality"]["edit"] == "trim" and trimmed_clip["review"] == "pending"
    assert trimmed_clip["start"] == round(merged["start"] + 0.25, 6)
    assert trimmed_clip["end"] == round(merged["start"] + 0.65, 6)
    with wave.open(io.BytesIO(clip_audio(merged).content)) as parent:
        parent.setpos(12000)
        expected = parent.readframes(19200)
    with wave.open(io.BytesIO(clip_audio(trimmed_clip).content)) as wav:
        assert wav.getnframes() == 19200
        assert wav.readframes(wav.getnframes()) == expected
    assert clip_audio(merged).status_code == 200
    assert client.get(buffer_url).json()["clips"] == []

    # Reuse an already-created range, as with merges made before replacement was supported.
    saved = post(client, source_base + "/clips", {"start": merged["start"], "end": third["end"]})
    reused = post(client, merge_url, {"clip_ids": [merged["id"], third["id"]]})
    assert reused["id"] == saved["id"] and reused["replaced_count"] == 2
    assert {row["id"] for row in client.get(buffer_url).json()["clips"]} == {merged["id"], third["id"]}
    assert clip_audio(merged).status_code == clip_audio(third).status_code == 200
    assert clip_audio(reused).content.startswith(b"RIFF")
    same_range = post(client, base + "/clips/" + reused["id"] + "/trim", {"start": 0.25, "end": 0.65})
    assert same_range["id"] == trimmed_clip["id"]

    trimmed = post(client, source_base + "/clips", {"start": 0.25, "end": 0.65})
    assert trimmed["duration"] == 0.4 and trimmed["quality"]["edit"] == "cut"
    with wave.open(io.BytesIO(clip_audio(trimmed).content)) as wav:
        assert wav.readframes(wav.getnframes()) == source_samples[12000:31200].astype("<i2").tobytes()
    assert client.post(source_base + "/clips", json={"start": 0, "end": info["duration"] + 0.2}).status_code == 400
    assert client.get(base).json()["review"]["pending"]["count"] == len(clips) + 1

    confirmed = {"clip_ids": [merged["id"], third["id"], reused["id"]]}
    with SessionLocal() as db:
        buffered_paths = [clip_path(db.get(RvcClip, item["id"])) for item in (merged, third)]
    replace_file = os.replace
    staged_attempts = []

    def fail_second_stage(source, destination):
        if Path(destination).suffix == ".deleting":
            staged_attempts.append(source)
            if len(staged_attempts) == 2:
                raise PermissionError("模拟第二段仍被占用")
        return replace_file(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr("processing.rvc_edit.os.replace", fail_second_stage)
        response = client.request("DELETE", buffer_url, json=confirmed)
        assert response.status_code == 409
    assert {row["id"] for row in client.get(buffer_url).json()["clips"]} == {merged["id"], third["id"]}
    assert all(path.is_file() and not path.with_suffix(".deleting").exists() for path in buffered_paths)

    response = client.request("DELETE", buffer_url, json=confirmed)
    assert response.status_code == 200 and response.json() == {"deleted": 2}
    assert client.get(buffer_url).json()["clips"] == []
    assert all(not path.exists() and not path.with_suffix(".deleting").exists() for path in buffered_paths)
    assert clip_audio(merged).status_code == clip_audio(third).status_code == 404
    assert clip_audio(reused).status_code == 200
    assert client.request("DELETE", buffer_url, json=confirmed).json() == {"deleted": 0}


def test_save_ranges_as_independent_wavs(client, speech, monkeypatch):
    _, source_file, _ = speech
    base = new_project(client)
    post(client, base + "/sources/local", {"kind": "local_audio", "paths": [str(source_file)]})
    post(client, base + "/process", {"split": False})
    source = wait_for_sources(client, base)[0]
    assert source["status"] == "done"
    before = client.get(base + "/clips").json()
    parent = before["clips"][0]
    parent_url = base + "/clips/" + parent["id"]
    trim_url = parent_url + "/trim"
    draft = {"ranges": [{"start": 2.0, "end": 3.25}, {"start": 0.5, "end": 1.5}, {"start": 4.0, "end": 5.5}]}
    directory = work_dir(base.rsplit("/", 1)[1], source["id"])
    existing_wavs = set(directory.rglob("*.wav"))

    def fail_second_write(raw, path, offset, start, normalize, *, manual=False):
        result = write_clip(raw, path, offset, start, normalize, manual=manual)
        if start == 24000:
            raise MediaError("模拟第二段写入失败")
        return result

    with monkeypatch.context() as patch:
        patch.setattr("processing.rvc_edit.write_clip", fail_second_write)
        assert client.post(trim_url, json=draft).status_code == 400
    assert client.get(base + "/clips").json()["total"] == before["total"]
    assert set(directory.rglob("*.wav")) == existing_wavs

    saved = post(client, trim_url, draft)["clips"]
    assert len({clip["id"] for clip in saved}) == len(draft["ranges"])
    with wave.open(io.BytesIO(client.get(parent_url + "/audio").content)) as original:
        for clip, selected in zip(saved, draft["ranges"]):
            start, end = (round(selected[key] * original.getframerate()) for key in ("start", "end"))
            original.setpos(start)
            result = client.get(base + "/clips/" + clip["id"] + "/audio")
            with wave.open(io.BytesIO(result.content)) as wav:
                assert wav.getnframes() == end - start
                assert wav.readframes(end - start) == original.readframes(end - start)
    assert post(client, trim_url, draft)["clips"] == saved
    assert client.get(base + "/clips").json()["total"] == before["total"] + len(saved)
    assert client.get(base + "/merge-buffer").json()["clips"] == []

    cuts = post(client, base + "/sources/" + source["id"] + "/clips", draft)["clips"]
    assert len(cuts) == 3 and all(clip["quality"]["edit"] == "cut" for clip in cuts)


def test_failed_download_retry_and_silence(client, speech, monkeypatch):
    folder, _, lines = speech
    silent = folder / "无声.wav"
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "4", silent)
    damaged = folder / "损坏.mp4"
    damaged.write_bytes(b"not a video")
    video_only = folder / "无音轨.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=160x90:r=2", "-t", "1", "-c:v", "mpeg4", video_only)
    base = new_project(client)
    report = post(client, base + "/sources/local", {"kind": "local_video", "paths": [str(damaged), str(video_only)]})
    assert len(report["errors"]) == 2 and report["added"] == 0
    assert post(client, base + "/sources/local", {"kind": "local_audio", "paths": [str(silent)]})["added"] == 1
    assert post(client, base + "/sources/online", {"videos": [{"url": VIDEO}]})["added"] == 1

    def failed(*args):
        from processing.rvc_audio import MediaError
        raise MediaError("mock download failure")

    monkeypatch.setattr("workers.rvc_worker.download", failed)
    post(client, base + "/process", {})
    sources = wait_for_sources(client, base)
    assert {item["status"] for item in sources} == {"failed"}
    online = next(item for item in sources if item["kind"] == "bilibili")
    assert "mock download failure" in online["error"]
    monkeypatch.setattr("workers.rvc_worker.download", lambda *_: lines[0])
    assert post(client, base + "/process", {"source_ids": [online["id"]], "split": False}) == {"queued": 1}
    sources = wait_for_sources(client, base)
    assert next(item for item in sources if item["id"] == online["id"])["status"] == "done"
    assert len(client.get(base + "/clips").json()["clips"]) == 1
