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
from fastapi.testclient import TestClient

from main import app
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
    retained = clips[:-1]
    assert client.patch(base + "/clips/review", json={
        "ids": [clip["id"] for clip in retained], "review": "kept",
    }).json()["updated"] == len(retained)
    assert client.patch(base + "/clips/review", json={"ids": [clips[-1]["id"]], "review": "excluded"}).status_code == 200

    output = post(client, base + "/export", {"sample_rate": 40000})
    wavs = Path(output["directory"])
    assert len(list(wavs.glob("*.wav"))) == len(retained) == output["count"]
    assert all(hashlib.sha256(file.read_bytes()).hexdigest() == digest for file, digest in hashes.items())
    archive = client.get(output["download_url"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert {"metadata.json", "sources.json", "quality_report.json"} <= set(bundle.namelist())
        with wave.open(io.BytesIO(bundle.read("wavs/000001.wav"))) as first:
            assert (first.getnchannels(), first.getsampwidth(), first.getframerate()) == (1, 2, 40000)


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
