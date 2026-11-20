# Sources of the sample and test videos

The ten sample clips are committed to this repository. Each row records where a clip came from and
the licence that allows its use. A clip is published as a sample when it is listed here and has an
entry in `data/samples.json` with its files; `infra/deploy_web.sh` copies them to the site.

| File | Title | URL | Licence |
|---|---|---|---|
| `neurolens_trailer_720p.mp4` | NeuroLens trailer, 1280x720 (75 s, the landing-page and public sample; rendered with Remotion and encoded with ffmpeg, H.264 CRF 22, AAC 128k, 8.6 MB) | Made by the NeuroLens team | Original work by the NeuroLens team (10 Nov 2026) |
| `sintel_trailer_720p.mp4` | Sintel trailer, 1280x720 (52 s, the public sample; Blender's 720p file, remuxed with ffmpeg for web start-up, no re-encode) | https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `bbb_720p_55s.mp4` | Big Buck Bunny, 55 s from 1:00, 1280x720 (the public sample; made from the 4K source file with ffmpeg, H.264 CRF 22, AAC 128k, 9.6 MB) | https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip | CC BY 3.0, "© copyright 2008, Blender Foundation \| www.bigbuckbunny.org" |
| `../warmup.mp4` | Sintel trailer, 3 s from 0:10, 640x360 (the GPU container's warm-up clip, baked into the image; made from the 720p sample with ffmpeg) | https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `cerave_30s.mp4` | CeraVe "Michael CeraVe" Super Bowl 2024 ad, 30 s broadcast cut (public sample) | https://www.youtube.com/watch?v=EVePyMTFciI | Third-party ad. Shown for non-commercial analysis and commentary; rights remain with CeraVe. |
| `cerave_76s.mp4` | CeraVe "Michael CeraVe" Super Bowl 2024 ad, 76 s extended cut (public sample) | https://www.youtube.com/watch?v=DwOpQHeWo4c | as the 30 s cut |
| `medishield_30s.mp4` | Ministry of Health Singapore "MediShield Life: Coverage for all, for life" 30 s cut (public sample) | https://www.youtube.com/watch?v=5DAyuar5cB0 | Third-party ad. Shown for non-commercial analysis and commentary; rights remain with Ministry of Health Singapore. |
| `medishield_60s.mp4` | Ministry of Health Singapore "MediShield Life: Coverage for all, for life" 60 s cut (public sample) | https://www.youtube.com/watch?v=QsqwqO_iDlI | as the 30 s cut |
| `tiktok_ZSg5soxrR.mp4` | Swee Choon Tim Sum TikTok video (public sample) | https://www.tiktok.com/@sweechoon.sg | Third-party clip. Shown for non-commercial analysis and commentary; rights remain with Swee Choon Tim Sum. |
| `tiktok_ZSg5tbDx8.mp4` | YouTrip Singapore TikTok video (public sample) | https://www.tiktok.com/@youtripsg | Third-party clip. Shown for non-commercial analysis and commentary; rights remain with YouTrip Singapore. |
| `tiktok_ZSg5tvxY9.mp4` | McDonald's Singapore x G-Dragon TikTok video (public sample) | https://www.tiktok.com/@mcdsg | Third-party clip. Shown for non-commercial analysis and commentary; rights remain with McDonald's Singapore. |
