#!/usr/bin/env bash
# Ships the last commit as the website (M3b §3c): the code of both web functions, then the page
# and its sample data into the bucket's site/ prefix, then clears CloudFront's cache so the new
# page shows at once. Run after `terraform apply` has created the functions.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION
cd "$(git rev-parse --show-toplevel)"

if [ -n "$(git status --porcelain)" ]; then
  echo "Uncommitted changes: commit first. Deploy ships the last commit only." >&2; exit 1
fi
TF="terraform -chdir=infra/terraform"
BUCKET=$($TF output -raw bucket_name)
DISTRIBUTION=$($TF output -raw cloudfront_distribution_id)

# 1. Code. Both functions run the same zip; wait until each update is live.
infra/build_web_lambda.sh
for fn in neurolens-web neurolens-stripe-webhook; do
  aws lambda update-function-code --function-name "$fn" --zip-file fileb://build/web_lambda.zip \
    --query LastUpdateStatus --output text >/dev/null
  aws lambda wait function-updated --function-name "$fn"
  echo "Updated $fn."
done

# 2. Site files, staged first so what is synced is exactly what is listed here.
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/static" "$STAGE/data"
git archive HEAD static | tar -x -C "$STAGE"
cp static/index.html "$STAGE/index.html"
# Only samples whose clip is listed in data/videos/SOURCES.md (open-licensed) are public; third-party
# ads never are. Their video and thumbnail go with them. None qualifies today: the list is empty.
python3 -I - "$STAGE" <<'PY'
import json, re, shutil, sys
from pathlib import Path

stage, data = Path(sys.argv[1]), Path("data")
listed = set(re.findall(r"\|\s*`([^`]+)`", (data / "videos/SOURCES.md").read_text()))
samples = json.loads((data / "samples.json").read_text())["samples"]
public = [s for s in samples if s["video_file"].removeprefix("videos/") in listed]
for s in public:
    for name in (s["video_file"], s.get("thumbnail")):
        if name:
            target = stage / "data" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(data / name, target)
(stage / "data/samples.json").write_text(json.dumps({"samples": public}))
print(f"{len(public)} of {len(samples)} samples are public.")
PY
aws s3 sync "$STAGE" "s3://$BUCKET/site/" --delete --only-show-errors
# The page itself: browsers must check for a new version on every load, or a tab can keep running
# an old page after a deploy (without Cache-Control they guess how long to keep it).
aws s3 cp "$STAGE/index.html" "s3://$BUCKET/site/index.html" --cache-control no-cache \
  --content-type "text/html; charset=utf-8" --only-show-errors
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths '/*' \
  --query Invalidation.Id --output text >/dev/null
echo "Deployed $(git rev-parse --short HEAD) to $($TF output -raw public_base_url)"
