# Sources of the sample and test videos

One line per clip we use (the files themselves are git-ignored). Record where it came from and the
licence it allows, so the report's data section is easy to write. Listing a clip here makes its sample public
(`infra/deploy_web.sh`): list a third-party clip only with the team's decision recorded in its row.

| File | Title | URL | Licence |
|---|---|---|---|
| `_test_clips/sintel_loop_119s_480p.mp4` | The 480p Sintel trailer (52 s), looped to 119.01 s with ffmpeg (the over-60 s and longest-clip test) | https://download.blender.org/durian/trailer/sintel_trailer-480p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `_test_clips/bbb_4k_55s_38mbps.mp4` | Big Buck Bunny, 3840x2160 30 fps, 55 s from 1:00, re-encoded H.264 at 38 Mbit/s (phone-like 4K, 263 MB; the 4K check) | https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip | CC BY 3.0, "© copyright 2008, Blender Foundation \| www.bigbuckbunny.org" |
| `neurolens_trailer_720p.mp4` | NeuroLens trailer, 1280x720 (75 s, the landing-page and public sample; rendered from `trailer/` and encoded with ffmpeg, H.264 CRF 22, AAC 128k, 8.6 MB) | `trailer/` (in this repository) | Original work by the NeuroLens team (10 Nov 2026) |
| `sintel_trailer_720p.mp4` | Sintel trailer, 1280x720 (52 s, the public sample; Blender's 720p file, remuxed with ffmpeg for web start-up, no re-encode) | https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `bbb_720p_55s.mp4` | Big Buck Bunny, 55 s from 1:00, 1280x720 (the public sample; made from the 4K clip above with ffmpeg, H.264 CRF 22, AAC 128k, 9.6 MB) | https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip | CC BY 3.0, "© copyright 2008, Blender Foundation \| www.bigbuckbunny.org" |
| `../warmup.mp4` | Sintel trailer, 3 s from 0:10, 640x360 (the GPU container's warm-up clip, baked into the image; made from the 720p sample with ffmpeg) | https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `cerave_30s.mp4` | CeraVe "Michael CeraVe" Super Bowl 2024 ad, 30 s broadcast cut (public sample) | https://www.youtube.com/watch?v=EVePyMTFciI | Third-party ad, all rights with CeraVe. Public sample by the team's decision (29 Oct 2026): non-commercial course project, analysis and commentary |
| `cerave_76s.mp4` | CeraVe "Michael CeraVe" Super Bowl 2024 ad, 76 s extended cut (public sample) | https://www.youtube.com/watch?v=DwOpQHeWo4c | as the 30 s cut |
| `medishield_30s.mp4` | Ministry of Health Singapore "MediShield Life: Coverage for all, for life" 30 s cut (public sample) | https://www.youtube.com/watch?v=5DAyuar5cB0 | Third-party ad, all rights with Ministry of Health Singapore. Public sample by the team's decision (10 Nov 2026): non-commercial course project, analysis and commentary |
| `medishield_60s.mp4` | Ministry of Health Singapore "MediShield Life: Coverage for all, for life" 60 s cut (public sample) | https://www.youtube.com/watch?v=QsqwqO_iDlI | as the 30 s cut |
| `gaze/tiktok_ZSg5soxrR.mp4` | Swee Choon Tim Sum TikTok video (public sample) | https://www.tiktok.com/@sweechoon.sg | Third-party clip, all rights with Swee Choon Tim Sum. Public sample by the team's decision (10 Nov 2026): non-commercial course project, analysis and commentary |
| `gaze/tiktok_ZSg5tbDx8.mp4` | YouTrip Singapore TikTok video (public sample) | https://www.tiktok.com/@youtripsg | Third-party clip, all rights with YouTrip Singapore. Public sample by the team's decision (10 Nov 2026): non-commercial course project, analysis and commentary |
| `gaze/tiktok_ZSg5tvxY9.mp4` | McDonald's Singapore x G-Dragon TikTok video (public sample) | https://www.tiktok.com/@mcdsg | Third-party clip, all rights with McDonald's Singapore. Public sample by the team's decision (10 Nov 2026): non-commercial course project, analysis and commentary |
