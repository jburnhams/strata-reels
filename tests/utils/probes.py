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


def name_chapter(params):
    """A chapter's name that says which scenes it was told of (their starts), so a test can read them back."""
    starts = [s["start"] for s in params["context"]["scenes"]]
    return {
        "text": "{}",
        "json": {
            "title": f"Scenes from {starts[0]}",
            "summary": f"scenes at {', '.join(starts)}",
            "inferred": [{"guess": "a birthday", "because": "a cake in every scene"}],
            "tags": ["birthday"],
            "mixed": False,
        },
        "tokens_in": 400,
        "tokens_out": 60,
    }


# Faces on the tape, followed shot by shot (strata-models' face-tracks): one person (A) in the first shot and again in
# the second, beside another (B), who is alone in the last. Two people seen together are never one.
NO_TRACKS = {"tracks": [], "together": [], "width": 720, "height": 576, "fps": 10.0, "model": "buffalo_l"}
A, B = [1.0, 0.0], [0.0, 1.0]
TRACKS = [
    # id, shot, start, end, who, box (x0, y0, x1, y1 in the video's pixels)
    (1, 0, 0.5, 3.5, A, (40, 100, 200, 300)),
    (2, 1, 4.5, 9.0, A, (40, 100, 120, 180)),
    (3, 1, 5.0, 8.0, B, (500, 120, 560, 180)),
    (4, 2, 11.0, 15.0, B, (300, 100, 420, 260)),
]


def face_tracks(params):
    """The tracks above, each with two stills whose frames are written into `out_dir` (as face-tracks writes them)."""
    out = params.get("out_dir")
    tracks = []
    for tid, shot, start, end, who, box in TRACKS:
        stills = []
        for t in (start + 0.5, end - 0.5):
            image = None
            if out:
                os.makedirs(out, exist_ok=True)
                image = os.path.join(out, f"t{tid}-{t:.3f}.jpg")
                with open(image, "wb") as f:
                    f.write(b"jpg")
            stills.append({"time": t, "bbox": list(box), "det_score": 0.9, "quality": 0.6, "embedding": who,
                           "image": image, "kps": [[box[0] + 10, box[1] + 10]], "pose": [0, 0, 0]})  # fmt: skip
        boxes = [[round(start + i * 0.5, 1), *box, 0.9] for i in range(int((end - start) / 0.5) + 1)]
        tracks.append({"id": tid, "shot": shot, "start": start, "end": end, "faces": len(boxes), "quality": 0.6,
                       "embedding": who, "boxes": boxes, "stills": stills})  # fmt: skip
    return {**NO_TRACKS, "tracks": tracks, "together": [[2, 3]], "cuts": params.get("cuts")}
