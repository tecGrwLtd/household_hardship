import numpy as np
import pandas as pd
import pytest

from backend.ml.allocate import allocate, budget_summary
from backend.ml.model import RANDOM_AUDIT_RATE


def scores(mid, width=1.0, amount=10.0, lo=None):
    mid = np.asarray(mid, dtype=float)
    return pd.DataFrame({
        "application_id": np.arange(len(mid)),
        "household_id": [f"h{i}" for i in range(len(mid))],
        "need_lo": mid - width if lo is None else np.asarray(lo, dtype=float),
        "need_mid": mid,
        "need_hi": mid + width,
        "amount_requested": amount,
    })


def test_budget_covering_everyone_defers_nobody(rng):
    ranked, cutoff = allocate(scores([5, 3, 1]), budget=30.0, rng=rng)
    assert cutoff == float("-inf")
    assert set(ranked["band"]) == {"auto_approve"}


def test_bands_follow_the_interval_rule(rng):
    ranked, cutoff = allocate(scores(np.arange(100, 0, -1), width=5.0), budget=200.0, rng=rng)

    assert cutoff == 80.0  # 20 applications fit; the 21st ranked (mid 80) is the first unfunded
    auto = ranked[ranked["band"] == "auto_approve"]
    review = ranked[ranked["band"] == "human_review"]
    assert (auto["need_lo"] > cutoff).all()
    assert ((review["need_lo"] <= cutoff) & (review["need_hi"] >= cutoff)).all()
    deferred = ranked[ranked["band"].isin(["defer", "audit_approve"])]
    assert (deferred["need_hi"] < cutoff).all()


def test_audit_sample_is_drawn_from_the_deferred(rng):
    ranked, _ = allocate(scores(np.arange(200, 0, -1), width=2.0), budget=100.0, rng=rng)
    n_below = int((ranked["band"].isin(["defer", "audit_approve"])).sum())
    assert (ranked["band"] == "audit_approve").sum() == round(n_below * RANDOM_AUDIT_RATE)


def test_auto_approval_never_commits_more_than_the_budget(rng):
    # Quantile crossing: every lower bound sits far above the cutoff, so the
    # interval rule alone would auto-approve all ten.
    ranked, _ = allocate(scores(np.arange(10, 0, -1), lo=np.full(10, 100.0)), budget=45.0, rng=rng)
    assert ranked.loc[ranked["band"] == "auto_approve", "amount_requested"].sum() <= 45.0
    assert (ranked["band"] == "human_review").any()


def test_allocation_is_reproducible_for_a_seed():
    s = scores(np.arange(300, 0, -1), width=2.0)
    a, _ = allocate(s, 500.0, np.random.default_rng(3))
    b, _ = allocate(s, 500.0, np.random.default_rng(3))
    pd.testing.assert_series_equal(a["band"], b["band"])


def test_empty_cycle(rng):
    ranked, cutoff = allocate(scores([]), budget=100.0, rng=rng)
    assert ranked.empty and cutoff == float("-inf")


def test_budget_summary():
    ranked = pd.DataFrame({
        "band": ["auto_approve", "audit_approve", "human_review", "defer"],
        "amount_requested": [40.0, 10.0, 30.0, 99.0],
    })
    assert budget_summary(ranked, 100.0) == pytest.approx(
        {"budget": 100.0, "committed": 50.0, "in_review": 30.0, "remaining": 50.0}
    )
