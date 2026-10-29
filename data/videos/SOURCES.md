# Sources of the sample and test videos

One line per clip we use (the files themselves are git-ignored). Record where it came from and the
licence it allows, so the report's data section is easy to write. Listing a clip here makes its sample public
(`infra/deploy_web.sh`): list a third-party clip only with the owner's decision recorded in its row.

| File | Title | URL | Licence |
|---|---|---|---|
| `_test_clips/sintel_trailer_480p.mp4` | Sintel trailer, 480p (52 s, with speech; the M2a image-build smoke clip) | https://download.blender.org/durian/trailer/sintel_trailer-480p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `_test_clips/sintel_loop_119s_480p.mp4` | The Sintel trailer above, looped to 119.01 s (the over-60 s and longest-clip test) | made from the trailer with ffmpeg | as the trailer |
| `_test_clips/bbb_4k_55s_38mbps.mp4` | Big Buck Bunny, 3840x2160 30 fps, 55 s from 1:00, re-encoded H.264 at 38 Mbit/s (phone-like 4K, 263 MB; the 4K check) | https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip | CC BY 3.0, "© copyright 2008, Blender Foundation \| www.bigbuckbunny.org" |
| `sintel_trailer_720p.mp4` | Sintel trailer, 1280x720 (52 s, the public sample; Blender's 720p file, remuxed with ffmpeg for web start-up, no re-encode) | https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `bbb_720p_55s.mp4` | Big Buck Bunny, 55 s from 1:00, 1280x720 (the public sample; made from the 4K clip above with ffmpeg, H.264 CRF 22, AAC 128k, 9.6 MB) | https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip | CC BY 3.0, "© copyright 2008, Blender Foundation \| www.bigbuckbunny.org" |
| `../warmup.mp4` | Sintel trailer, 3 s from 0:10, 640x360 (the GPU container's warm-up clip, baked into the image; made from the 720p sample with ffmpeg) | https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4 | CC BY 3.0, "© copyright Blender Foundation \| durian.blender.org" (https://durian.blender.org/sharing/) |
| `cerave_30s.mp4` | CeraVe "Michael CeraVe" Super Bowl 2024 ad, 30 s broadcast cut (public sample) | https://www.youtube.com/watch?v=EVePyMTFciI | Third-party ad, all rights with CeraVe. Public sample by Josh's decision (29 Oct 2026): non-commercial course project, analysis and commentary |
| `cerave_76s.mp4` | CeraVe "Michael CeraVe" Super Bowl 2024 ad, 76 s extended cut (public sample) | https://www.youtube.com/watch?v=DwOpQHeWo4c | as the 30 s cut |
