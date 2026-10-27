"""
Result JSON (schema v2) written by the worker to results/{user}/{job}/result.json.

v2 keeps every field the existing dashboard reads (`timesteps[*]`), and adds `raw`
(un-normalised ROI activations) so creative variants can be compared across videos —
per-video min-max scaling alone makes cross-video comparison meaningless.
"""

from pydantic import BaseModel

REGIONS = ("ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory")


class Regions(BaseModel):
    ffa_faces: float
    eba_bodies: float
    ppa_scenes: float
    sts_social: float
    auditory: float


class Timestep(BaseModel):
    t: int
    engagement_overall: float
    regions: Regions
    auditory_with_audio: float | None
    auditory_without_audio: float | None


class Summary(BaseModel):
    peak_t: int
    trough_t: int
    mean_engagement_raw: float
    audio_lift_mean_raw: float


class Processing(BaseModel):
    pass_full_s: float
    pass_silent_s: float
    gpu: str | None = None
    peak_vram_gb: float | None = None
    backend: str
    silent_mode: str


class ModelInfo(BaseModel):
    name: str
    revision: str | None = None


class ResultV2(BaseModel):
    schema_version: int = 2
    job_id: str
    filename: str
    duration_seconds: int
    model: ModelInfo
    timesteps: list[Timestep]
    raw: dict[str, list[float]]
    summary: Summary
    processing: Processing
