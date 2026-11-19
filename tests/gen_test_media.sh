#!/bin/sh
# Generate small synthetic test media for the v1.1 tests (no downloads).
# Run from the tests/ directory. All files are tiny and disposable.
set -e
cd "$(dirname "$0")"
mkdir -p media
# 12s 1280x720 video + 200 Hz "game audio" tone (the fake segment).
ffmpeg -v error -y -f lavfi -i testsrc=duration=12:size=1280x720:rate=30 \
  -f lavfi -i "sine=frequency=200:duration=12" -af "volume=0.5" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest media/seg-12s.mp4
# 12s 1000 Hz commentary tone (loud, so the mix is easy to detect).
ffmpeg -v error -y -f lavfi -i "sine=frequency=1000:duration=12" \
  -af "volume=0.8" -ar 48000 -ac 2 media/commentary-12s.wav
# Video-only file (no audio stream) for the hard-verification test.
ffmpeg -v error -y -f lavfi -i testsrc=duration=5:size=1280x720:rate=30 \
  -c:v libx264 -pix_fmt yuv420p media/video-only.mp4
# Corrupt file (random bytes) for the hard-verification test.
head -c 2048 /dev/urandom > media/corrupt.mp4
# Truncated valid file for the hard-verification test.
head -c 100000 media/seg-12s.mp4 > media/truncated.mp4
echo "test media ready:"
ls -la media/
