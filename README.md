# NeuroLens — Predicted Brain Response to Video

## Overview

NeuroLens uses Meta's **TRIBE v2** (TRImodal Brain Encoder) to predict how an average viewer's brain would respond to a video, second by second. It shows that **predicted brain response** relative to typical film and TV viewing: which brain systems respond (seeing, hearing, understanding), its strongest and weakest moments, how much depends on the soundtrack, and a 3D brain map synced to the video.

It is a virtual pre-test, not a prediction of views, retention or sales, and not emotion classification. The model gives no evidence for those, so NeuroLens makes no such claim.

---

## Where things live

- `neurolens/` — the Python package: settings, inference, response maths, storage, database, billing, the web app and the SageMaker container.
- `app.py`, `worker.py` — thin launchers (`python app.py`, port 5003; `python worker.py serve`).
- `static/` — the page, the hand-built SVG charts and the 3D brain assets.
- `data/` — sample results (`samples.json`), videos, thumbnails and generated output. See `data/videos/README.md`.
- `infra/` — Terraform, IAM policies, migrations, the worker image and the operator scripts. See `infra/README.md`.
- `experiments/` — the report experiments and side trials. See `experiments/README.md`.
- `notebooks/` — the original local prototype notebooks. See `notebooks/README.md`.
- `tests/` — pytest. `docs/` — specs, evidence, study materials and the submitted course documents. See `docs/README.md`.

---

## What it shows (current design, `docs/M3d_spec.md`)

The model outputs one row per second of video over 20,484 points of the cortex (the fsaverage5 surface: indices 0-10,241 left hemisphere, 10,242-20,483 right). TRIBE v2 was trained on fMRI responses standardised per point, so 0 means typical film/TV viewing: values are shown **signed and unscaled**, and two videos can be compared directly.

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

Each signal is the mean over its area's points; each group is the mean of its signals, and **overall** is the mean of the three groups. The worker also runs the video with its sound removed and computes the same 11 series from that pass (**muted**), cut to the shared length of the two passes.

**Brain-area map.** The areas come from the Glasser HCP-MMP1 parcellation (360 areas; Glasser et al. 2016, *Nature*). The two map files live in the weights prefix (`model-data/data/hcp_mmp/`, uploaded once by `infra/make_model_data.sh`); the container checks their SHA-256, so it never runs on a changed map. The map is Human Connectome Project data under the WU-Minn HCP Open Access Data Use Terms (see License).

**Result format 2** (`results/<job_id>.json`):

```
{"format": 2, "duration_seconds": T,
 "signals": {"faces": [T numbers], ..., "overall": [T numbers]},
 "muted":   {"faces": [M numbers], ..., "overall": [M numbers]},
 "job_id": ..., "processing_time_seconds": ..., "gpu"?: ..., "fake_inference"?: true}
```

Series order: faces, bodies, scenes, visual, sound, speech, social, see, hear, understand, overall. Values are rounded to 3 decimals. The web app adds a `summary` (opening, strongest and weakest moments, audio dependence) when it serves a result; it is never stored. Results without `"format": 2` are answered with 410 ("upload the video again").

**Raw brain file** (`results/<job_id>.brain`): the with-audio predictions as float16, shape (T, 20484), no header. The 3D map reads it, and later changes to the signals need no GPU rerun.

---

## Run it on a laptop

```bash
python infra/local_stack.py                     # whole product on a laptop: local PostgreSQL, fake S3/SQS, web on http://127.0.0.1:5003 (--grant <email> <dollars> adds credit)
FAKE_INFERENCE=1 python worker.py serve         # the SageMaker container on a laptop (fake model; real model needs a GPU)
pip install -e . -r requirements/dev.txt        # laptop: tests and lint only, no GPU or model needed
pytest && ruff check . && ruff format --check .
```

---

## Hardware Requirements

- **Production GPU:** AWS SageMaker (`infra/terraform/sagemaker.tf` names the instance types). The model peaks at about 20 GB of GPU memory; the endpoint's instance pools are chosen so each type fits it with at least 32 GB of RAM.
- **Laptop:** no GPU and no model needed. `python infra/local_stack.py` runs the whole product with fake analysis (`FAKE_INFERENCE=1`), and tests and lint need only Python.
- **Python:** 3.12 (`pyproject.toml`: `requires-python >=3.12,<3.14`).

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

`ffmpeg`/`ffprobe` are system tools (the container ships its own static ffprobe in a Lambda layer).

## Gated Model Access

The TRIBE v2 text encoder depends on **LLaMA 3.2-3B**, which is a gated model on HuggingFace. Before first run:

1. Go to [meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B) and accept the license
2. Create a HuggingFace read access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Put it in `.env` as `HF_TOKEN` (copy `.env.example`); `huggingface-cli login` also works for the notebooks

TRIBE v2 itself (`facebook/tribev2`) is freely downloadable under CC BY-NC 4.0.

---

## Deploying on AWS

The GPU runs in a SageMaker asynchronous endpoint. A deploy is three steps:

```bash
infra/build_image.sh                 # builds the image in CodeBuild and prints its digest
# put the digest in infra/terraform/terraform.tfvars as worker_image = "sha256:..."
terraform -chdir=infra/terraform apply
```

The website ships with `infra/deploy_web.sh`, and new database migrations with `infra/apply_schema.py` (`infra/README.md`).

See `infra/terraform/README.md`: setup from an empty account, work sessions and teardown.

---

## License

This project is for **non-commercial research use only**, in compliance with TRIBE v2's CC BY-NC 4.0 license.

Brain-area map: data were provided [in part] by the Human Connectome Project, WU-Minn Consortium (Principal Investigators: David Van Essen and Kamil Ugurbil; 1U54MH091657) funded by the 16 NIH Institutes and Centers that support the NIH Blueprint for Neuroscience Research; and by the McDonnell Center for Systems Neuroscience at Washington University. Parcellation: Glasser, M. F. et al. (2016), A multi-modal parcellation of human cerebral cortex, *Nature* 536, 171-178.
