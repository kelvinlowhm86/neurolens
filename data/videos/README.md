# Sample videos

The ten public sample clips (about 92 MB) live here and are committed, with their outputs under
`data/output/<id>/` (brain file, CSV, PDF, thumbnail), so a fresh clone plays every sample and
serves every download. `data/samples.json` holds each sample's inline result, summary and report,
and four stored comparisons (two sample pairs, each in both orders).

`infra/make_sample.py` rebuilds one sample's output folder from a finished job;
`infra/deploy_web.sh` publishes the samples to the site. `SOURCES.md` records the sources and licences.

## Expected filenames

```
bbb_720p_55s.mp4
cerave_30s.mp4
cerave_76s.mp4
medishield_30s.mp4
medishield_60s.mp4
neurolens_trailer_720p.mp4
sintel_trailer_720p.mp4
tiktok_ZSg5soxrR.mp4
tiktok_ZSg5tbDx8.mp4
tiktok_ZSg5tvxY9.mp4
```

Optional local test clips can go in `_test_clips/` (git-ignored).
