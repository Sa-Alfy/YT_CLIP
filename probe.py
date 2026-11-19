#!/usr/bin/env python3
"""Probe frames at a given timestamp in a video without downloading it.

Uses yt-dlp to resolve the direct video URL, then ffmpeg -ss to grab a
single frame via HTTP range seek. Used to confirm a candidate window
actually shows gameplay before committing to a full segment download.
"""
import json
import subprocess
import sys

YTD = ["./venv/bin/python", "-m", "yt_dlp"]


def direct_url(video_url):
    out = subprocess.run(
        YTD + ["-g", "-f", "bv*[height<=720]", "--no-playlist",
               "--sleep-requests", "1", video_url],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-300:])
    return out.stdout.strip().splitlines()[0]


def probe(video_url, t, out_path):
    url = direct_url(video_url)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", url,
           "-frames:v", "1", "-vf", "scale=390:-2", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:])
    return out_path


if __name__ == "__main__":
    # args: <discovery.json> <timestamp_s> [slug-prefix ...]
    disc = json.load(open(sys.argv[1]))
    t = float(sys.argv[2])
    picks = disc["youtube"][:3]
    for c in picks:
        slug = c["video_id"]
        out = f"verify/probe-{slug}-t{int(t)}.jpg"
        try:
            probe(c["url"], t, out)
            print(f"OK  {c['uploader']} t={int(t)}s -> {out}")
        except Exception as e:
            print(f"FAIL {c['uploader']} t={int(t)}s: {e}")
