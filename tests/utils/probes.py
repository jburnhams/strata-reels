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


# What Whisper says on the tape: a greeting in the first scene, a song in the second, and a stretch of hiss in the
# first that Whisper itself doubts (no_speech_prob 0.8), which no scene is told about.
SPEECH = {
    "language": "en",
    "language_probability": 0.97,
    "duration": 16.0,
    "segments": [
        {"start": 6.0, "end": 7.5, "text": " Say hello to Grandma!", "avg_logprob": -0.3, "no_speech_prob": 0.05,
         "words": [{"start": 6.0, "end": 6.3, "word": " Say", "probability": 0.9}]},
        {"start": 1.0, "end": 2.0, "text": " Thank you.", "avg_logprob": -0.9, "no_speech_prob": 0.8, "words": []},
        {"start": 11.0, "end": 13.0, "text": " Happy birthday to you", "avg_logprob": -0.2, "no_speech_prob": 0.01,
         "words": []},
    ],
}  # fmt: skip


def FACES(params):  # a role's answer, named like the others
    """Faces on each keyframe: two before frame 200, none after."""
    return {
        "images": [
            {
                "width": 640,
                "height": 480,
                "faces": [{"bbox": [10.0, 20.0, 60.0, 80.0], "det_score": 0.91}] * (2 if _frame(p) < 200 else 0),
            }
            for p in params["paths"]
        ]
    }


def describe_scene(params):
    """A description that says which frames and speech it was given, so a test can read them back."""
    frames = [_frame(p) for p in params["images"]]
    return {
        "text": "{}",
        "json": {
            "summary": f"frames {frames}",
            "observed": [params["context"]["speech"] or "no speech"],
            "inferred": [{"guess": "a birthday", "because": "a cake"}],
            "setting": "indoor",
            "people": 2,
            "children": 1,
            "activity": "talking",
            "tags": ["birthday"],
            "picture": {"quality": "fair", "problems": ["noisy"]},
        },
        "tokens_in": 900,
        "tokens_out": 120,
    }


def _frame(path):
    return int(os.path.basename(path)[:-4])
