# Docs

The specs describe the current design; history lives in git. Read the spec for the milestone you
are changing, and prefer the later spec where two disagree.

## Specs, in milestone order

| File | Read it when |
|---|---|
| `M0_spec.md` | changing the package layout, tooling or CI |
| `M1_spec.md` | changing uploads, the queue or the S3 helpers |
| `M2a_spec.md` | changing the worker image build or the inference-correction steps |
| `M2b_spec.md` | changing the job lifecycle, reliability or Experiments 1–2 |
| `M3a_spec.md` | changing the database, credit, billing or refunds |
| `M3b_spec.md` | changing sign-in, the AWS website, history or credit grants |
| `M3c_spec.md` | changing the academic scope, consent, sharing or deletion |
| `M3d_spec.md` | changing the result page, its charts or the 3D brain map |
| `M3e_spec.md` | changing GPU start-up, the container or the SageMaker resources |
| `M3f_spec.md` | changing batch upload, research sharing or sign-in sessions |
| `M3g_spec.md` | changing the AI report |
| `M3h_spec.md` | changing comparison reports or sample reports |
| `M3i_spec.md` | changing retention-curve collection |
| `M3j_spec.md` | changing YouTube checks or the sharing wording |
| `M3k_spec.md` | changing early-access requests or the curve reward rules |
| `M4_spec.md` | running the booth study, Experiments 1–3, the report or teardown |

## Other files and folders

- `session_checklist.md` — the steps for a GPU work session or the deployed demo window, in order, with each step's cost.
- `evidence/` — raw AWS and worker measurements behind the hardware, Spot and start-up choices, with dates.
- `study/` — the anonymous booth survey text, its Google Forms builder and the survey QR code.
- `course/` — the submitted course documents (`Preliminary.tex`, `Preliminary.pdf`, `Project Specification.pdf`); never edit.
- `*.local.*` — personal plans, notes and logs; git-ignored and never pushed.
