#!/usr/bin/env python3
"""Give your coding agent a video editor: probe, cut, remove silences, reframe, look. One file, local FFmpeg.

The core of kajisho5/ffmpeg-skill (MIT) in one standard-library script:
  probe   FILE                         measured duration, fps, size, audio (JSON)
  cut     FILE --from A --to B         lossless stream copy, or --accurate re-encode
  silence FILE [--threshold -35]       jump-cut dead air (or --list the cuts)
  fit     FILE --aspect 9:16 [--fit crop|pad|blur] [--duration 6 --method speed|trim]
  look    FILE                         contact sheet JPEG of the result, to check it
  demo    [DIR]                        make a synthetic clip and run every tool on it
Every edit prints one JSON line with the probed result, so the agent checks instead of guessing.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from fractions import Fraction

SIL_RE = re.compile(r"silence_(start|end): ([0-9.]+)")


def die(msg):
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)


def tool(name):
    path = shutil.which(name)
    if not path:
        die(f"{name} not found on PATH; install FFmpeg (https://ffmpeg.org/download.html)")
    return path


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        die(f"{os.path.basename(cmd[0])} failed: {p.stderr.strip()[-600:]}")
    return p


def ffmpeg(*args):
    return run([tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", *args])


def probe(path):
    if not os.path.exists(path):
        die(f"no such file: {path}")
    d = json.loads(run([tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]).stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    out = {"path": path, "duration": round(float(d["format"].get("duration", 0)), 3), "size_bytes": int(d["format"].get("size", 0))}
    if v:
        fps = Fraction(v.get("avg_frame_rate") or "0/1") if v.get("avg_frame_rate") not in (None, "0/0") else Fraction(0)
        out["video"] = {"codec": v["codec_name"], "width": v["width"], "height": v["height"], "fps": round(float(fps), 3)}
    if a:
        out["audio"] = {"codec": a["codec_name"], "sample_rate": int(a["sample_rate"]), "channels": a["channels"]}
    return out


def even(n):
    v = int(round(n))
    return v if v % 2 == 0 else v + 1


def frame_size(ratio, width, height, src_w, src_h):
    """ffmpeg-skill's sizing rule: both sides given are used; one side takes the other from the aspect;
    neither keeps the new aspect inside the source (9:16 from 16:9 is bounded by the source HEIGHT)."""
    src_ratio = Fraction(src_w, src_h)
    ratio = ratio or src_ratio
    if width and height:
        return even(width), even(height)
    if width:
        return even(width), even(even(width) / ratio)
    if height:
        return even(even(height) * ratio), even(height)
    if ratio <= src_ratio:
        return even(even(src_h) * ratio), even(src_h)
    return even(src_w), even(even(src_w) / ratio)


def parse_aspect(value):
    try:
        w, h = (int(x) for x in value.split(":"))
        return Fraction(w, h) if w > 0 and h > 0 else None
    except (ValueError, ZeroDivisionError):
        die(f"--aspect {value!r}: use W:H, e.g. 9:16")


def detect_silences(path, threshold, min_silence):
    err = ffmpeg("-i", path, "-vn", "-af", f"silencedetect=noise={threshold}dB:d={min_silence}", "-f", "null", "-").stderr
    spans, start = [], None
    for kind, val in SIL_RE.findall(err):
        if kind == "start":
            start = float(val)
        elif start is not None:
            spans.append((start, float(val)))
            start = None
    if start is not None:
        spans.append((start, float("inf")))
    return spans


def keep_ranges(silences, duration, margin, min_keep):
    """The ranges to keep: one cursor walking forward, keeping `margin` of silence beside speech."""
    keeps, cursor = [], 0.0
    for s, e in sorted(silences):
        s_adj = max(cursor, s + margin)
        if s_adj - cursor >= min_keep:
            keeps.append((cursor, s_adj))
        cursor = max(cursor, min(duration, e - margin) if e != float("inf") else duration)
    if duration - cursor >= min_keep:
        keeps.append((cursor, duration))
    return keeps


def stem(path, suffix):
    base, ext = os.path.splitext(path)
    return f"{base}_{suffix}{ext or '.mp4'}"


ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


def done(op, out, t0, **extra):
    print(json.dumps({"ok": True, "op": op, "output": probe(out), "seconds": round(time.perf_counter() - t0, 2), **extra}))
    return 0


def cmd_cut(a):
    t0, out = time.perf_counter(), a.output or stem(a.input, "cut")
    span = ["-ss", str(a.start), *(["-to", str(a.end)] if a.end else [])]
    if a.accurate:  # frame-exact: decode and re-encode
        ffmpeg("-i", a.input, *span, *ENC, out)
    else:  # lossless: starts on the keyframe at or before --from
        ffmpeg(*span, "-i", a.input, "-c", "copy", "-avoid_negative_ts", "make_zero", out)
    return done("cut", out, t0, mode="accurate" if a.accurate else "copy")


def cmd_silence(a):
    t0, meta = time.perf_counter(), probe(a.input)
    if "audio" not in meta:
        die("no audio stream: nothing to measure silence on")
    sil = detect_silences(a.input, a.threshold, a.min_silence)
    keeps = keep_ranges(sil, meta["duration"], a.margin, a.min_keep)
    kept = sum(e - s for s, e in keeps)
    plan = {"silences": [[round(s, 3), None if e == float("inf") else round(e, 3)] for s, e in sil],
            "keep": [[round(s, 3), round(e, 3)] for s, e in keeps], "kept_seconds": round(kept, 3),
            "removed_seconds": round(meta["duration"] - kept, 3)}
    if a.list:
        print(json.dumps({"ok": True, "op": "silence", "input": meta["duration"], **plan}))
        return 0
    if not keeps:
        die("everything is silence at this threshold; try a lower --threshold such as -45")
    out = a.output or stem(a.input, "tight")
    expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keeps)
    vf = f"select='{expr}',setpts=N/FRAME_RATE/TB"
    # Tag the kept audio frames and select them: what ffmpeg-skill does instead of aselect,
    # which passes every frame through on FFmpeg 8.0/8.0.1.
    af = f"ametadata=mode=add:key=keep:value=1:enable='{expr}',ametadata=mode=select:key=keep,asetpts=N/SR/TB"
    ffmpeg("-i", a.input, *(["-vf", vf] if "video" in meta else []), "-af", af, *ENC, out)
    return done("silence", out, t0, removed_seconds=plan["removed_seconds"], cuts=len(sil))


def atempo_chain(factor):
    parts = []
    while factor > 2.0:
        parts.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    return ",".join(parts + [f"atempo={factor:.6f}"])


def cmd_fit(a):
    t0, meta = time.perf_counter(), probe(a.input)
    if "video" not in meta:
        die("no video stream to fit")
    vf, af, pre, post = [], [], [], []
    if a.duration:
        if a.method == "speed":
            factor = meta["duration"] / a.duration
            if not 1 / a.max_speed <= factor <= a.max_speed:
                die(f"needs {factor:.2f}x speed, above --max-speed {a.max_speed}; use --method trim")
            vf.append(f"setpts={1 / factor:.8f}*PTS")
            if "audio" in meta:
                af.append(atempo_chain(factor))
        elif a.duration < meta["duration"]:
            pre = ["-ss", f"{(meta['duration'] - a.duration) / 2 if a.from_center else 0:.3f}"]
        post = ["-t", f"{a.duration:.3f}"]
    if a.aspect or a.width or a.height:
        w, h = frame_size(parse_aspect(a.aspect) if a.aspect else None, a.width, a.height, meta["video"]["width"], meta["video"]["height"])
        if a.fit == "crop":
            vf += [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}"]
        elif a.fit == "blur":  # landscape in the middle of a blurred, dimmed copy of itself
            vf.append(f"split[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:2,"
                      f"eq=brightness=-0.15[bg2];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fg2];"
                      f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2")
        else:
            vf += [f"scale={w}:{h}:force_original_aspect_ratio=decrease", f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:{a.pad_color}"]
        vf.append("setsar=1")
    if not vf and not af and not post:
        die("nothing to do: pass --aspect, --width/--height or --duration")
    out = a.output or stem(a.input, "fit")
    ffmpeg(*pre, "-i", a.input, *(["-vf", ",".join(vf)] if vf else []), *(["-af", ",".join(af)] if af else []), *post, *ENC, out)
    return done("fit", out, t0)


def cmd_look(a):
    t0, meta = time.perf_counter(), probe(a.input)
    if "video" not in meta:
        die("no video stream to look at")
    n = a.cols * a.rows
    out = a.output or os.path.splitext(a.input)[0] + "_sheet.jpg"
    step = max(meta["duration"] / n, 0.04)
    ffmpeg("-i", a.input, "-vf", f"fps=1/{step:.4f},scale={a.width}:-2,tile={a.cols}x{a.rows}:padding=4:color=black",
           "-frames:v", "1", "-q:v", "3", out)
    print(json.dumps({"ok": True, "op": "look", "output": out, "frames": n, "every_seconds": round(step, 3),
                      "seconds": round(time.perf_counter() - t0, 2)}))
    return 0


def cmd_probe(a):
    print(json.dumps(probe(a.input), indent=2))
    return 0


def cmd_demo(a):
    """Synthetic 12 s 16:9 clip with three silent gaps, then silence -> fit 9:16 -> look."""
    os.makedirs(a.dir, exist_ok=True)
    src = os.path.join(a.dir, "talk.mp4")
    # A tone that is silent for 1.5 s three times: the "speech" a jump cut should keep.
    tone = "if(between(t,2.5,4)+between(t,6,7.5)+between(t,9,10.5),0,0.4*sin(2*PI*440*t))"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12", "-f", "lavfi",
           "-i", f"aevalsrc='{tone}':s=48000:d=12", "-shortest", *ENC, src)
    for argv in (["silence", src], ["fit", os.path.join(a.dir, "talk_tight.mp4"), "--aspect", "9:16", "--fit", "blur"],
                 ["look", src], ["look", os.path.join(a.dir, "talk_tight_fit.mp4"), "--cols", "4", "--rows", "1"]):
        main(argv)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("input")
    p = sub.add_parser("cut"); p.add_argument("input"); p.add_argument("--from", dest="start", type=float, default=0.0)
    p.add_argument("--to", dest="end", type=float); p.add_argument("--accurate", action="store_true"); p.add_argument("-o", "--output")
    p = sub.add_parser("silence"); p.add_argument("input"); p.add_argument("-o", "--output")
    p.add_argument("--threshold", type=float, default=-35.0, help="silence level in dBFS (default -35)")
    p.add_argument("--min-silence", type=float, default=0.6, help="only remove gaps at least this long (s)")
    p.add_argument("--margin", type=float, default=0.15, help="silence kept on each side of speech (s)")
    p.add_argument("--min-keep", type=float, default=0.2, help="drop kept pieces shorter than this (s)")
    p.add_argument("--list", action="store_true", help="print the cut list only, write nothing")
    p = sub.add_parser("fit"); p.add_argument("input"); p.add_argument("-o", "--output")
    p.add_argument("--aspect"); p.add_argument("--width", type=int); p.add_argument("--height", type=int)
    p.add_argument("--fit", choices=["crop", "pad", "blur"], default="crop"); p.add_argument("--pad-color", default="black")
    p.add_argument("--duration", type=float); p.add_argument("--method", choices=["speed", "trim"], default="speed")
    p.add_argument("--from-center", action="store_true"); p.add_argument("--max-speed", type=float, default=4.0)
    p = sub.add_parser("look"); p.add_argument("input"); p.add_argument("-o", "--output")
    p.add_argument("--cols", type=int, default=4); p.add_argument("--rows", type=int, default=3); p.add_argument("--width", type=int, default=320)
    p = sub.add_parser("demo"); p.add_argument("dir", nargs="?", default="demo-out")
    a = ap.parse_args(argv)
    return globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
