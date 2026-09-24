import numpy as np
import pandas as pd
import pytest

from backend.ml.drift import drift_report, psi, reference_profile
from backend.ml.model import LGBMNeedModel
from backend.ml.registry import load, save
from backend.ml.repeat import HistoryRepeatModel, evaluate_repeat, repeat_labels
from tests.test_model import _toy


def test_repeat_labels_need_a_full_year_of_follow_up():
    apps = pd.DataFrame({
        "application_id": [1, 2, 3, 4, 5],
        "household_id": ["a", "a", "b", "c", "c"],
        "submitted_at": pd.to_datetime(["2024-01-01", "2024-06-01", "2024-02-01", "2024-03-01", "2026-01-01"], utc=True),
    })
    lab = repeat_labels(apps).set_index("application_id")
    assert bool(lab.loc[1, "returned"]) and lab.loc[1, "days_to_next"] == 152
    assert not lab.loc[3, "returned"]                        # never came back
    assert not lab.loc[4, "returned"]                        # came back, but after 671 days
    assert lab.loc[[1, 2, 3, 4], "observable"].all()
    assert not lab.loc[5, "observable"]                      # less than a year before the latest application


def _history_frame(n=1500, seed=0):
    rng = np.random.default_rng(seed)
    helped = rng.random(n) < 0.3
    prior = rng.integers(0, 5, n)
    f = pd.DataFrame({
        "prior_applications_count": prior,
        "days_since_last_application": np.where(prior > 0, rng.integers(30, 700, n), np.nan),
        "status": np.where(helped, "awarded", "deferred"),
    })
    p = 1 / (1 + np.exp(-(0.5 + 0.4 * prior - 1.5 * helped)))
    return f, (rng.random(n) < p).astype(int), rng.integers(0, 10, n)


def test_history_model_learns_and_round_trips(tmp_path):
    f, y, _ = _history_frame()
    m = HistoryRepeatModel().fit(f, y)
    p = m.predict_proba(f)
    assert 0 < p.min() < p.max() < 1
    assert m.coef[3] < 0 < m.coef[0]      # funded -> less likely back; more history -> more likely
    save(m, "repeat-test", {}, {}, models_dir=tmp_path)
    np.testing.assert_allclose(load("repeat_history", tmp_path / "repeat-test").predict_proba(f), p)


def test_evaluate_repeat_picks_the_lower_brier_and_only_calibrates_when_it_helps():
    f, y, groups = _history_frame()
    r = evaluate_repeat(f, y, groups, candidates={"repeat_history": HistoryRepeatModel})
    m = r["models"]["repeat_history"]
    assert r["best"] == "repeat_history" and r["gates"]["passed"]
    assert m["brier"] <= m["brier_raw"]
    assert m["brier"] < r["brier_base_rate"]


def test_psi_is_zero_for_identical_distributions_and_grows_with_shift():
    assert psi([0.25] * 4, [0.25] * 4) == pytest.approx(0.0)
    assert psi([0.25] * 4, [0.4, 0.3, 0.2, 0.1]) > 0.1


def test_drift_report_is_quiet_on_the_same_population_and_flags_a_shift():
    f, y = _toy(n=1500)
    m = LGBMNeedModel(params={"n_estimators": 50}).fit(f, y)
    ref = reference_profile(m, f)

    same = drift_report(m, ref, f.sample(600, random_state=1))
    assert same["score_psi"] < 0.1 and not any(s["level"] == "shift" for s in same["features_shifted"])

    poorer = f.sample(600, random_state=2).copy()
    poorer["monthly_income"] = poorer["monthly_income"] * 0.3
    shifted = drift_report(m, ref, poorer)
    assert shifted["status"] == "alert"
    assert "monthly_income" in {s["feature"] for s in shifted["features_shifted"]}
