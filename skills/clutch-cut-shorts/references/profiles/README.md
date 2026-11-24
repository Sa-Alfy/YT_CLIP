# Streamer Profiles

One file per streamer, named `<streamer>.json`.

Each file records the streamer's verified source dimensions and frame rate, the facecam and gameplay source crops and output destinations, the caption height percentage, the date the rects were last verified, and notes.

Verification rule: verify every profile with an extracted frame on each build, since streamers change overlays. A crop that no longer matches the current overlay fails gate G4.
