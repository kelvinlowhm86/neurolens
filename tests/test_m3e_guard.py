"""M3e GPU guard (docs/M3e_spec.md §6c, §11 "Guard").

The guard reads SageMaker and Application Auto Scaling through small recording clients, and the
jobs table through PostgreSQL. It stops the endpoint only when an instance is running, no
keep-warm holds it, and neither a live claim, a dispatched request, nor any recent job activity
says work is still coming.
"""

import logging

from neurolens import billing

ENDPOINT = "neurolens-gpu-test"


class FakeSageMaker:
    def __init__(self, instances):
        self.instances = instances
        self.endpoints = []
        self.updates = []

    def describe_endpoint(self, **kwargs):
        self.endpoints.append(kwargs)
        return {"ProductionVariants": [{"CurrentInstanceCount": self.instances}]}

    def update_endpoint_weights_and_capacities(self, **kwargs):
        self.updates.append(kwargs)
        return {}


class FakeAppScaling:
    def __init__(self, min_capacity):
        self.min_capacity = min_capacity
        self.calls = []

    def describe_scalable_targets(self, **kwargs):
        self.calls.append(kwargs)
        return {"ScalableTargets": [{"MinCapacity": self.min_capacity}]}


def gpu_guard():
    from neurolens.lambdas import gpu_guard

    return gpu_guard


def run_check(db, *, instances=1, min_capacity=0):
    module = gpu_guard()
    sagemaker = FakeSageMaker(instances)
    appscaling = FakeAppScaling(min_capacity)
    stopped = module.check(db, sagemaker=sagemaker, appscaling=appscaling, endpoint=ENDPOINT)
    return stopped, sagemaker, appscaling


def stop_call():
    return {
        "EndpointName": ENDPOINT,
        "DesiredWeightsAndCapacities": [{"VariantName": "main", "DesiredInstanceCount": 0}],
    }


def test_no_instance_means_no_stop(db):
    stopped, sagemaker, _ = run_check(db, instances=0)
    assert stopped is False
    assert sagemaker.updates == []


def test_keep_warm_blocks_the_stop(db):
    stopped, sagemaker, _ = run_check(db, instances=1, min_capacity=1)
    assert stopped is False
    assert sagemaker.updates == []


def test_a_live_claim_blocks_the_stop(db, new_job):
    job_id, _ = new_job()
    assert billing.claim(db, job_id) == 1
    stopped, sagemaker, _ = run_check(db)
    assert stopped is False
    assert sagemaker.updates == []


def test_a_dispatched_queued_job_blocks_even_after_thirty_quiet_minutes(db, pg, new_job):
    job_id, _ = new_job()
    pg.conn.execute(
        "UPDATE jobs SET verified_duration_ms = 3000 WHERE job_id = %s::uuid", (job_id,)
    )
    pg.age(job_id, updated_s=31 * 60, created_s=2 * 60 * 60)
    stopped, sagemaker, _ = run_check(db)
    assert stopped is False
    assert sagemaker.updates == []


def test_an_undispatched_queued_job_quiet_for_thirty_minutes_does_not_block(db, pg, new_job):
    job_id, _ = new_job()
    pg.age(job_id, updated_s=31 * 60, created_s=2 * 60 * 60)
    stopped, sagemaker, _ = run_check(db)
    assert stopped is True
    assert sagemaker.updates == [stop_call()]


def test_a_processing_row_with_a_stale_heartbeat_does_not_block(db, pg, new_job):
    job_id, _ = new_job()
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=31 * 60, created_s=5 * 60 * 60)
    stopped, sagemaker, _ = run_check(db)
    assert stopped is True
    assert sagemaker.updates == [stop_call()]


def test_any_job_updated_within_thirty_minutes_blocks(db, pg, new_job):
    job_id, _ = new_job()
    assert billing.settle_success(db, job_id) is True
    pg.age(job_id, updated_s=10 * 60, created_s=60 * 60)
    stopped, sagemaker, _ = run_check(db)
    assert stopped is False
    assert sagemaker.updates == []


def test_all_clear_stops_once_and_logs_the_marker(db, caplog):
    caplog.set_level(logging.DEBUG)
    stopped, sagemaker, appscaling = run_check(db)
    assert stopped is True
    assert sagemaker.updates == [stop_call()]
    assert "GPU_GUARD_STOPPED" in caplog.text
    call = appscaling.calls[0]
    assert call["ServiceNamespace"] == "sagemaker"
    assert call["ResourceIds"] == [f"endpoint/{ENDPOINT}/variant/main"]
    assert call["ScalableDimension"] == "sagemaker:variant:DesiredInstanceCount"


def test_handler_stops_the_endpoint_named_in_the_environment(aws, db, monkeypatch):
    from neurolens import lambdas

    module = gpu_guard()
    sagemaker = FakeSageMaker(instances=1)
    appscaling = FakeAppScaling(min_capacity=0)
    monkeypatch.setenv("NEUROLENS_GPU_ENDPOINT", ENDPOINT)
    monkeypatch.setattr(lambdas, "database", lambda: db)
    monkeypatch.setattr(lambdas, "sagemaker_client", lambda: sagemaker)
    monkeypatch.setattr(lambdas, "appscaling_client", lambda: appscaling)

    module.handler({}, None)

    assert sagemaker.updates == [stop_call()]
    assert sagemaker.endpoints == [{"EndpointName": ENDPOINT}]
    assert appscaling.calls[0]["ResourceIds"] == [f"endpoint/{ENDPOINT}/variant/main"]
