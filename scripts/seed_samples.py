"""
Upload the sample gallery (videos + thumbnails referenced by data/samples.json) to s3://<bucket>/samples/.
The web tier serves them with presigned URLs (SAMPLES_SOURCE=s3).

    python scripts/seed_samples.py <bucket>
"""

import json
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    bucket = sys.argv[1]
    s3 = boto3.client("s3")
    samples = json.loads((DATA / "samples.json").read_text())["samples"]
    uploaded = missing = 0
    for s in samples:
        for rel, ctype in ((s["video_file"], "video/mp4"), (s.get("thumbnail"), "image/jpeg")):
            if not rel:
                continue
            src = DATA / rel
            if not src.exists():
                print(f"  missing locally, skipped: {rel}")
                missing += 1
                continue
            s3.upload_file(str(src), bucket, f"samples/{rel}", ExtraArgs={"ContentType": ctype})
            uploaded += 1
    print(f"Uploaded {uploaded} files to s3://{bucket}/samples/ ({missing} missing locally)")


if __name__ == "__main__":
    main()
