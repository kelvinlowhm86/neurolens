# Sample videos

This directory is gitignored except this file and `SOURCES.md`; the clips themselves are about 71 MB.
`data/samples.json` holds the four public samples (each with its inline result, summary and
report), and the thumbnails under `data/output/` are pushed, so the dashboard renders its charts on
a fresh clone. Everything else is git-ignored: on a fresh clone a sample's video file, brain file,
CSV and PDF (under `data/output/<id>/`) are missing, so video playback and the brain map, CSV and
PDF downloads need the files restored. `infra/make_sample.py` rebuilds one sample's output folder
from a finished job; `infra/deploy_web.sh` publishes the samples it finds here to the site. Drop
the .mp4s back in here to restore playback. `SOURCES.md` records the sources and licences.

## Expected filenames

```
bbb_720p_55s.mp4
cerave_30s.mp4
cerave_76s.mp4
sintel_trailer_720p.mp4
```

`_test_clips/` holds test clips, such as `sintel_loop_119s_480p.mp4` (the 119 s clip) and short
clips like `ok_27s.mp4`. `gaze/` holds the side-trial clips (vertical TikTok ads and a music
video) that `experiments/` reads. Both folders are local (git-ignored); the `gaze/` clips are
third-party and not redistributable.
