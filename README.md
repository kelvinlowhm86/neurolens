# NeuroLens

See how a viewer's brain would respond to your video, second by second, before you publish it.

[![CI](https://github.com/kelvinlowhm86/neurolens/actions/workflows/ci.yml/badge.svg)](https://github.com/kelvinlowhm86/neurolens/actions/workflows/ci.yml) [![Live site](https://img.shields.io/badge/live-try%20it-orange)](https://df6girqyykyfa.cloudfront.net) [![Non-commercial](https://img.shields.io/badge/use-non--commercial-lightgrey)](#license)

<!-- TRAILER VIDEO: dragged in on github.com -->

[🏆 Best Project – Platinum, CS5224 track, 29th NUS STEPS (Nov 2026)](https://uvents.nus.edu.sg/event/29th-steps)

**Try it live:** https://df6girqyykyfa.cloudfront.net (the samples open without signing in).

## What it does

NeuroLens is built on Meta's TRIBE v2 (TRImodal Brain Encoder) and predicts, second by second, how an average viewer's brain would respond to a video. It shows which brain systems respond (seeing, hearing and understanding), the strongest and weakest moments, how much depends on the soundtrack, and a 3D brain synced to the video.

### Features

- Analysis charts and a 3D brain, synced to the video.
- An AI-written report with timed advice on what to keep and what to change.
- A written comparison of two videos, including two cuts of one.
- Real audience retention beside the prediction once a shared video reaches 1,000 views on YouTube or Facebook.
- Production-style billing: credit is reserved at upload, charged only when a result is ready and refunded on any failure.
- Scale-to-zero GPU on AWS.

![NeuroLens analysis view](docs/images/neurolens_app.png)

*The analysis view of the CeraVe ad sample (© CeraVe, shown for non-commercial analysis).*

**Built with:** Python, Flask, PyTorch (TRIBE v2), Three.js, AWS (SageMaker, Lambda, S3, SQS, Aurora, Cognito, CloudFront, Bedrock), Terraform, GitHub Actions.

<img src="docs/images/steps_award.jpg" alt="NeuroLens at the 29th NUS STEPS awards" width="600">

*At the 29th NUS STEPS, where NeuroLens won Best Project (Platinum) in the CS5224 track.*

## How it works

![Architecture](docs/images/architecture.svg)

You sign in, upload a video, and the browser sends the file straight to S3. A queue message wakes a dispatcher, which checks credit and hands the video to the GPU worker. The GPU starts only when there is work and stops when idle. The worker runs TRIBE v2 with audio and again without it, turns both predictions into the signals below, and writes a result the page draws.

<!-- WALKTHROUGH VIDEO: dragged in on github.com -->

The full design is in [docs/architecture.md](docs/architecture.md).

### What the signals measure

NeuroLens reads 7 signals from the brain areas the TRIBE v2 paper tested, in 3 groups:

| Signal | Brain areas (Glasser HCP-MMP1) | Group |
|---|---|---|
| faces | FFC | see |
| bodies | V4t | see |
| scenes | PHA1-3 | see |
| visual | V1-3 | see |
| sound | A1, LBelt, MBelt, PBelt | hear |
| speech | A5, STSva, STSvp, 45 (left only) | understand |
| social | PGi, TE1a | understand |

Each signal is the average over its brain area, each group averages its signals, and overall averages the three groups. 0 means typical film or TV viewing, so two videos can be compared directly. A second run with the sound removed shows how much of the response depends on the soundtrack.

The values, the brain-area map and the result files are described in [Brain signals and result files](docs/architecture.md#brain-signals-and-result-files).

## CS5224 write-up

[Final report](docs/course/Final_report.pdf) · [Final slides](docs/course/Final_slides.pdf)

## Limits

NeuroLens predicts an average viewer's perception, not views, retention, sales or emotion; the full list is under [Known limitations](docs/architecture.md#known-limitations).

## For developers

### Run it on a laptop

The real model needs a GPU with about 20 GB of memory, gated LLaMA access and AWS. To work on everything around it for free, the local stack runs the whole product on a laptop (sign-in, upload, credit, queue, result page) with fake S3 and SQS and a stand-in model that returns made-up numbers. To see real results, use the [live site](https://df6girqyykyfa.cloudfront.net).

You need Python 3.12 and Docker (for the database).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e . -r requirements/dev.txt

# A local PostgreSQL on port 55432, used by the tests and the local stack (once; afterwards `docker start neurolens-test-pg`)
docker run -d --name neurolens-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
export NEUROLENS_TEST_DSN=postgresql://postgres:test@localhost:55432/postgres

python infra/local_stack.py                     # whole product, fake S3/SQS and fake analysis: http://127.0.0.1:5003 (--grant <email> <minutes> adds credit)

# or only the GPU container, with the stand-in model:
FAKE_INFERENCE=1 python worker.py serve
```

While working, run the file for the part you changed (`pytest tests/test_response.py`); it takes seconds. The whole suite (`pytest && ruff check . && ruff format --check .`) runs on GitHub on every push and pull request (`.github/workflows/ci.yml`). Without `NEUROLENS_TEST_DSN` the database tests are skipped, not failed, so set it before trusting a local run.

### Deploy on AWS

The operator guide is [docs/operating.md](docs/operating.md); the human-only first steps are in [infra/iam/ACCOUNT_OWNER.md](infra/iam/ACCOUNT_OWNER.md) and [infra/terraform/README.md](infra/terraform/README.md). From an empty account, the first deploy is one command:

```bash
infra/setup.sh
```

It lists every missing prerequisite before changing anything, and a second run skips finished work. The first run downloads the model files (about 18 GB) to the laptop to upload them, so it needs that much free disk. `infra/teardown.sh` stops the GPU, destroys the stack and checks for leftovers.

#### Gated model access

The TRIBE v2 text encoder depends on LLaMA 3.2-3B, which is a gated model on Hugging Face. Before first run:

1. Go to [meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B) and accept the license.
2. Create a Hugging Face read access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
3. Put it in `.env` as `HF_TOKEN` (copy `.env.example`); `huggingface-cli login` also works for the prototype notebooks.

TRIBE v2 itself (`facebook/tribev2`) is freely downloadable under CC BY-NC 4.0.

#### Hardware

| Machine | What it needs |
|---|---|
| Production GPU | AWS SageMaker asynchronous endpoint (`infra/terraform/sagemaker.tf` names the instance types). The model peaks at about 20 GB of GPU memory; every instance type leaves at least 32 GB of system memory. |
| Laptop | No GPU and no model: `python infra/local_stack.py` runs the whole product with fake analysis. Tests and lint need only Python. |
| Python | 3.12 (`pyproject.toml`: `requires-python >=3.12,<3.14`). |

#### Dependencies

The dependency lists live in `requirements/`, one file per machine type:

| File | What for |
|---|---|
| `base.txt` | shared by the worker and CI (NumPy is pinned to the version tribev2 forces) |
| `web.txt` | the web Lambda functions (Flask, boto3, Authlib, fpdf2) |
| `worker.txt` | the SageMaker container without the model (nilearn, boto3) |
| `model.txt` | TRIBE v2 and its encoders (GPU machines and the real-import check) |
| `prototype.txt` | the two prototype notebooks (the model plus Jupyter and matplotlib) |
| `lambdas.txt` | the report Lambda's Anthropic SDK layer |
| `dev.txt` | laptop and CI: tests and lint (pytest, ruff, moto, psycopg, responses, joserfc, scikit-image) |

`ffmpeg`/`ffprobe` are system tools (the worker image installs `ffmpeg`; the dispatcher Lambda uses a static `ffprobe` layer).

### Where things live

- [`neurolens/`](neurolens/): the Python package for settings, inference, response maths, storage, database, billing, the web app and the SageMaker container.
- [`app.py`](app.py), [`worker.py`](worker.py): thin launchers (`python app.py`, port 5003; `python worker.py serve`).
- [`static/`](static/): the page, the hand-built SVG charts and the 3D brain assets.
- [`data/`](data/): sample results (`samples.json`), videos, thumbnails and generated output. See [`data/videos/README.md`](data/videos/README.md).
- [`infra/`](infra/): Terraform, IAM policies, migrations, the worker image and the operator scripts. See [`infra/README.md`](infra/README.md).
- [`prototype/`](prototype/): the original local prototype notebooks. See [`prototype/README.md`](prototype/README.md).
- [`requirements/`](requirements/): one dependency list per machine type.
- [`tests/`](tests/): pytest; [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs them on every push.
- [`docs/`](docs/): [architecture.md](docs/architecture.md) (design), [operating.md](docs/operating.md) (running it), `evidence/`, `course/`, `images/` and [tribev2_reference.md](docs/tribev2_reference.md). See [`docs/README.md`](docs/README.md).
- [`trailer/`](trailer/): the launch video, made with Remotion; separate from the product. See [`trailer/README.md`](trailer/README.md).

## License

Licensed under [CC BY-NC 4.0](LICENSE): free to use and adapt with attribution, for non-commercial purposes only, matching TRIBE v2's own license.

Brain-area map: data were provided [in part] by the Human Connectome Project, WU-Minn Consortium (Principal Investigators: David Van Essen and Kamil Ugurbil; 1U54MH091657) funded by the 16 NIH Institutes and Centers that support the NIH Blueprint for Neuroscience Research; and by the McDonnell Center for Systems Neuroscience at Washington University. Parcellation: Glasser, M. F. et al. (2016), A multi-modal parcellation of human cerebral cortex, *Nature* 536, 171-178.
