"""What ffprobe says about a digitised PAL tape, given instead of run in the unit tests (tests/integration runs the real
one). On the `pythonpath` (pyproject.toml), so any test imports it as `probes`."""

import os

from strata_python import media

TAPE = {
    "format": {"duration": "5400.04", "format_name": "matroska,webm"},
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 576,
            "r_frame_rate": "25/1",
            "field_order": "tt",
        },
        {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 2},
    ],
}


def answer(raw):
    def probe(path):
        if raw is None:
            raise media.MediaError("Invalid data found when processing input")
        return media.Probe.from_json(path, raw)

    return probe


# What strata-models answers about the tape in these tests: 400 frames at 25 fps, cut at frames 100 and 250, black
# from 9.6 s to 10 s (frames 240 to 250). Keyframes before frame 200 look like one place, the rest like another, so
# the first two shots are one scene and the third another.
SHOTS = {
    "cuts": [{"time": 4.0, "frame": 100, "score": 0.5}, {"time": 10.0, "frame": 250, "score": 0.9}],
    "shots": [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 10.0}, {"start": 10.0, "end": 16.0}],
    "black": [{"start": 9.6, "end": 10.0}],
    "fps": 25.0,
    "frames": 400,
    "duration": 16.0,
    "threshold": 0.3,
    "min_shot_s": 0.6,
    "model": "ffmpeg-scene-score",
}


def embeddings(params):
    """A vector per keyframe, from its frame number in the file name: [1, 0] before frame 200, [0, 1] after."""
    return {
        "images": [[1.0, 0.0] if int(os.path.basename(p)[:-4]) < 200 else [0.0, 1.0] for p in params["paths"]],
        "dim": 2,
    }
