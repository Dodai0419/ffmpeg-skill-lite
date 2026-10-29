---
name: ffmpeg-skill-lite
description: Edit video files with local FFmpeg through typed commands, checking each result. Probe a file, cut a range (lossless or frame-exact), remove silences and dead air, reframe to 9:16 / 1:1 / any aspect (crop, pad or blur fill), fit to a duration, and make a contact sheet. Use when someone asks to trim, jump-cut, make a Short/Reel/TikTok version, or check a video.
---

# ffmpeg-skill-lite

One script, `ffmpeg_lite.py`. It needs `ffmpeg` and `ffprobe` on PATH. Every command prints JSON. Read it, never assume.

## Workflow
1. **Probe first:** `python ffmpeg_lite.py probe IN.mp4`. Decide from the measured duration, size, fps and audio.
2. **Edit** with one command at a time:
   - Cut: `python ffmpeg_lite.py cut IN.mp4 --from 12 --to 30` (lossless, starts on a keyframe). Add `--accurate` for a frame-exact cut.
   - Dead air: `python ffmpeg_lite.py silence IN.mp4 --list` to see the plan, then run it without `--list`.
     For quiet rooms use `--threshold -40`. For only long pauses use `--min-silence 1`.
   - Vertical: `python ffmpeg_lite.py fit IN.mp4 --aspect 9:16 --fit blur` (or `crop` / `pad`).
   - Length: `python ffmpeg_lite.py fit IN.mp4 --duration 30 --method trim --from-center`, or `--method speed`.
   - Use `-o OUT.mp4` to name outputs. By default they are `<name>_cut/_tight/_fit.mp4`.
3. **Verify:** check `output.duration` and `output.video.width/height` in the JSON against what was asked.
   Then `python ffmpeg_lite.py look OUT.mp4` and open the JPEG to see the picture.
4. If a command prints `"ok": false`, read `error` and fix the input. Do not retry it unchanged.

## Limits
No captions, loudness, overlays or colour work. Never pass raw FFmpeg filters. The tool does not accept them.
