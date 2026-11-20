"""infra/local_stack.py --grant: amounts are typed in whole minutes."""

import importlib.util
from pathlib import Path

import pytest
from conftest import ISSUER
from neurolens import billing

LOCAL_STACK = Path(__file__).resolve().parent.parent / "infra" / "local_stack.py"


def _local_stack():
    """infra/local_stack.py is a script, not a package module: load it by path."""
    spec = importlib.util.spec_from_file_location("local_stack_under_test", LOCAL_STACK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_stack_grant_uses_whole_minutes(db, pg, make_cfg):
    cfg = make_cfg()
    user_id = billing.sign_in(db, ISSUER, "sub-local", "ann@example.com", 0)
    _local_stack()._grant(db, cfg, "ann@example.com", "2")
    assert pg.balance(user_id) == (400, 0)


@pytest.mark.parametrize("minutes", ["0", "-1", "2.5", "5m", "abc", ""])
def test_local_stack_grant_refuses_non_positive_or_non_whole_minutes(db, pg, make_cfg, minutes):
    cfg = make_cfg()
    billing.sign_in(db, ISSUER, "sub-local", "ann@example.com", 0)
    before = pg.snapshot()
    with pytest.raises(SystemExit):
        _local_stack()._grant(db, cfg, "ann@example.com", minutes)
    assert pg.snapshot() == before
