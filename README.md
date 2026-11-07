# NeuroLens

See how a viewer's brain would respond to your video, second by second, before you publish it.

[![CI](https://github.com/kelvinlowhm86/neurolens/actions/workflows/ci.yml/badge.svg)](https://github.com/kelvinlowhm86/neurolens/actions/workflows/ci.yml) [![Live site](https://img.shields.io/badge/live-try%20it-orange)](https://df6girqyykyfa.cloudfront.net) [![Non-commercial](https://img.shields.io/badge/use-non--commercial-lightgrey)](#license)

![NeuroLens analysis view](docs/images/neurolens_app.png)

*The analysis view of the CeraVe ad sample (© CeraVe, shown for non-commercial analysis).*

**Try it live:** https://df6girqyykyfa.cloudfront.net (the samples open without signing in).

## What it does

NeuroLens is built on Meta's TRIBE v2 (TRImodal Brain Encoder). It predicts, second by second, how an average viewer's brain would respond to a video. It shows which brain systems respond (seeing, hearing and understanding), the strongest and weakest moments, how much depends on the soundtrack, and a 3D brain synced to the video.

### Features

- Analysis charts and a 3D brain, synced to the video.
- An AI-written report with timed advice on what to keep and what to change.
- A written comparison of two videos, including two cuts of one.
- Real audience retention beside the prediction once a shared video reaches 1,000 views on YouTube or Facebook.
- Pay per use with credits.
- Scale-to-zero GPU on AWS.

## Quick start

No GPU, model or AWS account needed. Python 3.12 and Docker (for the database).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e . -r requirements/dev.txt

# A local PostgreSQL on port 55432, used by the tests and the local stack (once; afterwards `docker start neurolens-test-pg`)
docker run -d --name neurolens-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
export NEUROLENS_TEST_DSN=postgresql://postgres:test@localhost:55432/postgres

python infra/local_stack.py                     # whole product, fake S3/SQS and fake analysis: http://127.0.0.1:5003 (--grant <email> <dollars> adds credit)
FAKE_INFERENCE=1 python worker.py serve         # only the GPU container, with a fake model
```

While working, run the file for the part you changed (`pytest tests/test_m3i_curves.py`); it takes seconds. The whole suite (`pytest && ruff check . && ruff format --check .`, about 6 minutes) runs on GitHub on every push and pull request (`.github/workflows/ci.yml`). Without `NEUROLENS_TEST_DSN` the database tests are skipped, not failed, so set it before trusting a local run.

## Where things live

- [`neurolens/`](neurolens/): the Python package for settings, inference, response maths, storage, database, billing, the web app and the SageMaker container.
- [`app.py`](app.py), [`worker.py`](worker.py): thin launchers (`python app.py`, port 5003; `python worker.py serve`).
- [`static/`](static/): the page, the hand-built SVG charts and the 3D brain assets.
- [`data/`](data/): sample results (`samples.json`), videos, thumbnails and generated output. See [`data/videos/README.md`](data/videos/README.md).
- [`infra/`](infra/): Terraform, IAM policies, migrations, the worker image and the operator scripts. See [`infra/README.md`](infra/README.md).
- [`experiments/`](experiments/): the report experiments and side trials. See [`experiments/README.md`](experiments/README.md).
- [`notebooks/`](notebooks/): the original local prototype notebooks. See [`notebooks/README.md`](notebooks/README.md).
- [`tests/`](tests/): pytest; [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs them on every push. [`docs/`](docs/): specs, evidence, study materials and the submitted course documents. See [`docs/README.md`](docs/README.md).
- [`docs/architecture.html`](docs/architecture.html): the architecture page, one clickable diagram of how it runs on AWS (download the repo and open it in a browser; GitHub shows it as code).
- [`trailer/`](trailer/): the launch video, made with Remotion; separate from the product. See [`trailer/README.md`](trailer/README.md).

## How it works

The model outputs one row per second of video over 20,484 points of the cortex (the fsaverage5 surface: indices 0-10,241 left hemisphere, 10,242-20,483 right). TRIBE v2 was trained on fMRI responses standardised per point, so 0 means typical film/TV viewing: values are shown signed and unscaled, and two videos can be compared directly.

`neurolens/response.py` reads 7 signals from the areas the TRIBE v2 paper tested, in 3 groups:

| Signal | Brain areas (Glasser HCP-MMP1) | Group |
|---|---|---|
| faces | FFC | see |
| bodies | V4t | see |
| scenes | PHA1-3 | see |
| visual | V1-3 | see |
| sound | A1, LBelt, MBelt, PBelt | hear |
| speech | A5, STSva, STSvp, 45 (left only) | understand |
| social | PGi, TE1a | understand |

Each signal is the mean over its area's points; each group is the mean of its signals, and overall is the mean of the three groups. The worker also runs the video with its sound removed and computes the same 11 series from that pass (muted), cut to the shared length of the two passes.

The brain-area map comes from the Glasser HCP-MMP1 parcellation (360 areas; Glasser et al. 2016, *Nature*). The two map files live in the weights prefix (`model-data/data/hcp_mmp/`, uploaded once by `infra/make_model_data.sh`); the container checks their SHA-256, so it never runs on a changed map. The map is Human Connectome Project data under the WU-Minn HCP Open Access Data Use Terms (see License).

Result format 2 (`results/<job_id>.json`):

```
{"format": 2, "duration_seconds": T,
 "signals": {"faces": [T numbers], ..., "overall": [T numbers]},
 "muted":   {"faces": [M numbers], ..., "overall": [M numbers]},
 "job_id": ..., "processing_time_seconds": ..., "gpu"?: ..., "fake_inference"?: true}
```

Series order: faces, bodies, scenes, visual, sound, speech, social, see, hear, understand, overall. Values are rounded to 3 decimals. The web app adds a `summary` (the sound share, `audio_dependence`) when it serves a result; it is never stored. Results without `"format": 2` are answered with 410 ("upload the video again").

The raw brain file (`results/<job_id>.brain`) holds the with-audio predictions as float16, shape (T, 20484), with no header. The 3D map reads it, and later changes to the signals need no GPU rerun.

## Hardware Requirements

- Production GPU: AWS SageMaker (`infra/terraform/sagemaker.tf` names the instance types). The model peaks at about 20 GB of GPU memory; the endpoint's instance pools are chosen so each type fits it with at least 32 GB of RAM.
- Laptop: no GPU and no model needed. `python infra/local_stack.py` runs the whole product with fake analysis (`FAKE_INFERENCE=1`), and tests and lint need only Python.
- Python: 3.12 (`pyproject.toml`: `requires-python >=3.12,<3.14`).

## Dependencies

The dependency lists live in `requirements/`, one file per machine type (`AGENTS.md` has the setup command):

| File | What for |
|---|---|
| `base.txt` | shared by the worker and CI (NumPy is pinned to the version tribev2 forces) |
| `web.txt` | the web Lambda functions (Flask, boto3, Authlib, fpdf2) |
| `worker.txt` | the SageMaker container without the model (nilearn, boto3) |
| `model.txt` | TRIBE v2 and its encoders (GPU machines and the real-import check) |
| `notebooks.txt` | the two notebooks (the model plus Jupyter and matplotlib) |
| `experiments.txt` | the experiment scripts (Locust, matplotlib, requests) |
| `lambdas.txt` | the report Lambda's Anthropic SDK layer |
| `dev.txt` | laptop and CI: tests and lint (pytest, ruff, moto, psycopg, responses, joserfc, scikit-image) |

`ffmpeg`/`ffprobe` are system tools (the worker image installs `ffmpeg`; the dispatcher Lambda uses a static `ffprobe` layer).

## Gated Model Access

The TRIBE v2 text encoder depends on LLaMA 3.2-3B, which is a gated model on HuggingFace. Before first run:

1. Go to [meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B) and accept the license
2. Create a HuggingFace read access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Put it in `.env` as `HF_TOKEN` (copy `.env.example`); `huggingface-cli login` also works for the notebooks

TRIBE v2 itself (`facebook/tribev2`) is freely downloadable under CC BY-NC 4.0.

## Deploying on AWS

Two roles appear in these docs. **The account owner** owns the AWS account and grants access. **The operator** sets up and deploys NeuroLens in that account, and also handles alerts and access requests (the account-owner guide, `infra/iam/ACCOUNT_OWNER.md`, calls them the deployer).

The GPU runs in a SageMaker asynchronous endpoint. After the first-time setup (`infra/terraform/README.md`), shipping a new GPU image is three steps:

```bash
infra/build_image.sh                 # builds the image in CodeBuild and prints its digest
# put the digest in infra/terraform/terraform.tfvars as worker_image = "sha256:..."
terraform -chdir=infra/terraform apply
```

The website ships with `infra/deploy_web.sh`, and new database migrations with `infra/apply_schema.py` (`infra/README.md`).

See `infra/terraform/README.md`: setup from an empty account, work sessions and teardown.

## Limits

Predictions describe an average viewer's brain response compared with typical film and TV viewing; they are not forecasts of views, retention or sales, and NeuroLens does not read emotions.

## Design docs

The specs and evidence live in [`docs/`](docs/README.md).

## License

This project is for non-commercial research use only, in compliance with TRIBE v2's CC BY-NC 4.0 license.

Brain-area map: data were provided [in part] by the Human Connectome Project, WU-Minn Consortium (Principal Investigators: David Van Essen and Kamil Ugurbil; 1U54MH091657) funded by the 16 NIH Institutes and Centers that support the NIH Blueprint for Neuroscience Research; and by the McDonnell Center for Systems Neuroscience at Washington University. Parcellation: Glasser, M. F. et al. (2016), A multi-modal parcellation of human cerebral cortex, *Nature* 536, 171-178.
