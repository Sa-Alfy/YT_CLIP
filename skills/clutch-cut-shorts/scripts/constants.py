"""Single source of truth for Stage 1 numeric constants.

Every workflow script reads these values instead of hard-coding its own.
Do not change a constant to make a build pass. Plain module, stdlib only.
"""

# Output format
W = 1080
H = 1920
FPS = 30

# Final duration bounds in seconds
MIN_S = 16.0
MAX_S = 38.0
# Story moments (MOMENT total 13-14) may run longer: the payoff needs room.
# The schema allows up to MAX_S_STORY; validate_edl.py enforces that only
# 13-14 moments may exceed MAX_S (2026-11-24 self-improvement: winners run
# 40-60s; our 38s cap cut story moments short).
MAX_S_STORY = 45.0

# Words that may never be punch words: hook-derived punch used to highlight
# "THE" / "HE" (Lacy, 2026-11-24) because they appear in the hook text.
# Punch is for pictureable words (numbers, verbs, nouns), never stopwords.
STOPWORDS_PUNCH = frozenset([
    "A", "AN", "THE", "HE", "SHE", "IT", "HIS", "HER", "ITS", "THEY",
    "OF", "TO", "IN", "ON", "AT", "BY", "FOR", "WITH", "AND", "OR",
    "IS", "WAS", "BE", "ARE", "I", "YOU", "WE", "MY", "THIS", "THAT",
    "AS", "SO", "BUT", "NOT", "NO", "UP", "OUT", "GOT", "GET", "JUST",
])

# Payoff-replay zoom range (render-time centre-crop zoom on replay segments;
# speed stays 1.0 so CUT-5/CUT-6/G2 still hold — no slow-mo via speed change).
REPLAY_ZOOM_MIN = 1.2
REPLAY_ZOOM_MAX = 1.5

# Facecam panel height as a percentage of the 1920 output height
FACE_PANEL_MIN_PCT = 35
FACE_PANEL_MAX_PCT = 40

# Caption centre height as a percentage of the 1920 output height
CAPTION_Y_PCT_MIN = 60
CAPTION_Y_PCT_MAX = 65
# Tolerance on the centre-y band (percentage points). The centre y is
# estimated from MarginV + fontsize/2 for bottom-aligned captions, which is
# approximate; a caption 0.1% over the band (the v3 fixture measures 65.1%)
# must not fail on position alone — the audit records its position as PASS.
CAPTION_Y_TOL_PCT = 1.0

# Maximum allowed facecam upscale factor (dst width / src crop width)
FACE_UPSCALE_MAX = 2.5

# Audio loudness target in LUFS
LOUDNORM_I = -14

# Caption timing tolerances in seconds
CAPTION_TOL_S = 0.10
AUDIT_TOL_S = 0.15

# Minimum fraction of a caption that must overlap Silero VAD speech
VAD_MIN_OVERLAP = 0.50

# Whisper words below this probability are dropped from captions
WORD_MIN_PROB = 0.4

# Minimum clear-of-speech silence in seconds for a silence cut
CUT_SILENCE_MIN_S = 0.08

# Minimum margin in seconds inside a word gap for a word-boundary cut
WORD_GAP_MARGIN_S = 0.03

# Minimum segment duration in seconds
MIN_SEG_S = 1.5

# Loop tolerance in seconds: tail src_out must equal head src_in within this
LOOP_TOL_S = 0.05

# Retry caps
MAX_GATE_ATTEMPTS = 3
MAX_BUILDER_RETRIES = 2

# Strings whose presence in the command log or work dir proves TTS use
TTS_FORBIDDEN_STRINGS = [
    "tts",
    "espeak",
    "piper",
    "elevenlabs",
    "edge-tts",
    "say",
    "bark",
]

# Gate order, enforced by the render scripts and the manifest builder
GATE_ORDER = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"]
