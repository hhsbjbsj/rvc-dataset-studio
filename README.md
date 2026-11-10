# RVC Dataset Studio

A local Windows workbench for turning audio and video into organized RVC training datasets.

[简体中文安装与使用指南](README.zh-CN.md) · [Download for Windows x64](https://github.com/hhsbjbsj/rvc-dataset-studio/releases/latest)

- Keep sources, clips, and ZIP export history together in a project for each character or speaker.
- Import local audio or video, pick an audio track, and automatically slice speech. Bilibili and YouTube links use yt-dlp and depend on source availability.
- Review clips by ear, merge split lines, and fine-tune several selections on one waveform. Loop or slow down a selection while adjusting it, then save each selection as a separate WAV at the original speed.
- Export only the clips you select, download earlier batches from History, or convert existing audio and import the result into a project.
- Switch the launcher and workbench between Simplified Chinese, English, and Japanese. The launcher supports custom wallpapers and GitHub release checks.

## Get started

1. Download and extract the Windows x64 ZIP to a drive with room for your media and exports.
2. Make sure Python 3.11 or 3.12 (with the Windows `py` launcher), Node.js 22.12 or newer, and FFmpeg/FFprobe are on PATH.
3. Open `RvcLauncher.exe` from the extracted folder. Run **Set up studio** once, then click **Launch studio**.

Setup needs internet access to install dependencies. Local audio processing and export run on your PC. See the [Chinese guide](README.zh-CN.md) for source builds, keyboard shortcuts, and storage locations.

The app prepares training audio; it does not train RVC models, isolate vocals from music, or automatically identify speakers. Review mixed tracks before exporting.

This independent project builds on [Beatrice Dataset WebUI for Windows](https://github.com/Project-Beatrice-V2/Beatrice-dataset-webui-windows). The original copyright notice is retained in the [MIT license](LICENSE).
