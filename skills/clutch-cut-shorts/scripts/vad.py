#!/home/hatch/workspace/transcribe-venv/bin/python
"""Silero VAD on the picture-lock audio (design G6, rule VAD-1).

Runs the Silero VAD ONNX model with onnxruntime -- no torch is installed or
required. Model resolution, in order:
  1. --model <path to a silero_vad.onnx file>
  2. $SILERO_VAD_MODEL
  3. ~/.cache/clutch-cut/silero_vad.onnx, downloading it on first use from
     https://raw.githubusercontent.com/snakers4/silero-vad/v4.0/files/silero_vad.onnx
     (the v4.0 ONNX model; the master branch no longer hosts that file, and
     its interface is input/sr/h/c with h/c [2, 1, 64] -- verified by loading
     it with onnxruntime).
The bundled faster_whisper silero_vad_v6.onnx asset is deliberately NOT used:
it has a different (LSTM, 576-sample) interface whose call semantics are not
verified here.

Audio is decoded with PyAV and resampled to 16 kHz mono float32. Speech is
detected in 512-sample (32 ms) windows with threshold 0.5, min speech 250 ms,
min silence 100 ms, and 30 ms padding -- the Silero reference defaults.
Writes vad_final.json:
    {"method": "silero_vad",
     "speech": [{"start": float, "end": float}],
     "total_speech_s": float}
Speech timestamps are in audio seconds.

Usage:
    /home/hatch/workspace/transcribe-venv/bin/python vad.py \\
        --audio picture_lock.mp4 --out vad_final.json

Exit code 0 on success, 1 on failure. Prints a JSON result to stdout.
"""
import argparse
import json
import os
import sys
import urllib.request

VENV_PYTHON = "/home/hatch/workspace/transcribe-venv/bin/python"
MODEL_URL = ("https://raw.githubusercontent.com/snakers4/silero-vad/"
             "v4.0/files/silero_vad.onnx")
DEFAULT_CACHE = os.path.expanduser("~/.cache/clutch-cut/silero_vad.onnx")

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512          # 32 ms at 16 kHz
THRESHOLD = 0.5
MIN_SPEECH_MS = 250
MIN_SILENCE_MS = 100
SPEECH_PAD_MS = 30


def resolve_model(explicit_path):
    """Return a local .onnx path, downloading to the cache dir if needed."""
    if explicit_path:
        if not os.path.isfile(explicit_path):
            raise RuntimeError("model file not found: %s" % explicit_path)
        return explicit_path
    env_path = os.environ.get("SILERO_VAD_MODEL")
    if env_path:
        if not os.path.isfile(env_path):
            raise RuntimeError("SILERO_VAD_MODEL points at a missing file: %s" % env_path)
        return env_path
    if os.path.isfile(DEFAULT_CACHE):
        return DEFAULT_CACHE
    os.makedirs(os.path.dirname(DEFAULT_CACHE), exist_ok=True)
    tmp_path = DEFAULT_CACHE + ".download"
    try:
        urllib.request.urlretrieve(MODEL_URL, tmp_path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("could not download Silero VAD model from %s: %s "
                           "(pass --model with a local silero_vad.onnx)"
                           % (MODEL_URL, exc))
    if os.path.getsize(tmp_path) < 100_000:
        os.remove(tmp_path)
        raise RuntimeError("downloaded Silero VAD model is suspiciously small; "
                           "refusing to use it")
    os.replace(tmp_path, DEFAULT_CACHE)
    return DEFAULT_CACHE


def decode_audio_16k_mono(path):
    """Decode any audio file to 16 kHz mono float32 numpy array via PyAV.

    Uses AudioFrame.to_ndarray() (NOT bytes(frame.planes[0]): the plane
    buffer is alignment-padded, so taking the whole buffer inflates the
    sample count -- on one test file 695,264 samples instead of the true
    576,598 -- and the padding bytes are uninitialised, which made VAD
    results non-deterministic between runs). Flushes the resampler at the
    end so trailing samples are not lost.
    """
    import av
    import numpy as np

    container = av.open(path)
    stream = next((s for s in container.streams if s.type == "audio"), None)
    if stream is None:
        raise RuntimeError("no audio stream in %s" % path)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks = []

    def _collect(frame):
        for chunk in resampler.resample(frame):
            chunks.append(np.asarray(chunk.to_ndarray()).reshape(-1))

    for frame in container.decode(stream):
        _collect(frame)
    _collect(None)  # flush the resampler
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
    return pcm


def detect_speech(session, audio):
    """Run the Silero VAD ONNX model over 16 kHz mono audio.

    Model interface (verified against the v4.0 file from snakers4/silero-vad):
    inputs  input [1, N] float32, sr scalar int64, h [2, 1, 64] float32,
            c [2, 1, 64] float32; outputs output [1, 1] speech probability,
            hn [2, 1, 64], cn [2, 1, 64]. (This is NOT the older combined
    "state" [2, 1, 128] interface some tutorials show; that file no longer
    exists at the master URL, which is why this script pins the v4.0 tag.)

    Returns [(start_s, end_s)] using the Silero reference defaults.
    """
    import numpy as np

    state_h = np.zeros((2, 1, 64), dtype=np.float32)
    state_c = np.zeros((2, 1, 64), dtype=np.float32)
    sr = np.array(SAMPLE_RATE, dtype=np.int64)
    window_s = WINDOW_SAMPLES / SAMPLE_RATE
    min_speech_windows = max(1, int(MIN_SPEECH_MS / 1000 / window_s))
    min_silence_windows = max(1, int(MIN_SILENCE_MS / 1000 / window_s))
    pad_s = SPEECH_PAD_MS / 1000.0

    n_windows = len(audio) // WINDOW_SAMPLES
    segments = []
    triggered = False
    current_start = 0
    temp_end = 0
    silence_run = 0
    for i in range(n_windows):
        chunk = audio[i * WINDOW_SAMPLES:(i + 1) * WINDOW_SAMPLES]
        ort_inputs = {"input": chunk[np.newaxis, :].astype(np.float32),
                      "sr": sr, "h": state_h, "c": state_c}
        prob, state_h, state_c = session.run(["output", "hn", "cn"], ort_inputs)
        is_speech = float(prob[0][0]) >= THRESHOLD
        t = i * window_s
        if is_speech:
            if not triggered:
                triggered = True
                current_start = t
            silence_run = 0
        else:
            if triggered:
                silence_run += 1
                if silence_run == 1:
                    temp_end = t
                if silence_run >= min_silence_windows:
                    end = temp_end
                    if (end - current_start) >= min_speech_windows * window_s:
                        segments.append((current_start, end))
                    triggered = False
                    silence_run = 0
    if triggered:
        end = n_windows * window_s
        if (end - current_start) >= min_speech_windows * window_s:
            segments.append((current_start, end))

    audio_len_s = len(audio) / SAMPLE_RATE
    padded = []
    for start, end in segments:
        padded.append((max(0.0, round(start - pad_s, 3)),
                       min(audio_len_s, round(end + pad_s, 3))))
    return padded


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Silero VAD (ONNX, no torch) on picture-lock audio; writes vad_final.json.",
        epilog="MUST be run under the transcribe venv python: %s "
               "(system python3 has no onnxruntime/av). Example: %s vad.py "
               "--audio picture_lock.mp4 --out vad_final.json. The Silero VAD "
               "ONNX model is downloaded on first use from %s to "
               "~/.cache/clutch-cut/silero_vad.onnx (or pass --model)."
               % (VENV_PYTHON, VENV_PYTHON, MODEL_URL),
    )
    parser.add_argument("--audio", required=True, help="Picture-lock file to analyse.")
    parser.add_argument("--out", required=True, help="Output path for vad_final.json.")
    parser.add_argument("--model", default=None,
                        help="Local silero_vad.onnx file (default: download to the cache dir).")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.audio):
        print(json.dumps({"ok": False, "error": "audio file not found: %s" % args.audio}))
        return 1

    try:
        import numpy as np  # noqa: F401
        import onnxruntime as ort
        model_path = resolve_model(args.model)
        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        audio = decode_audio_16k_mono(args.audio)
        if len(audio) == 0:
            raise RuntimeError("decoded audio is empty: %s" % args.audio)
        speech = detect_speech(session, audio)
    except ImportError as exc:
        print(json.dumps({"ok": False,
                          "error": "missing dependency under this interpreter: %s. "
                                   "Run this script with %s" % (exc, VENV_PYTHON)}))
        return 1
    except Exception as exc:  # noqa: BLE001 - report any model/audio failure as JSON
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    total = round(sum(end - start for start, end in speech), 3)
    result_data = {
        "method": "silero_vad",
        "speech": [{"start": start, "end": end} for start, end in speech],
        "total_speech_s": total,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2)
        f.write("\n")

    result = {"ok": True, "out": args.out, "method": "silero_vad",
              "speech_segments": len(speech), "total_speech_s": total,
              "model": model_path}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
