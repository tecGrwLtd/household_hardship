import numpy as np
import pandas as pd
import pytest

from backend.ml.model import (
    LGBMNeedModel, conformal_adjustment, cross_conformal, out_of_fold_predict, welfare_weights,
)
from backend.ml.registry import load, save


def test_welfare_weights_favour_the_worst_off():
    # target is poverty_gap: higher = worse off = heavier weight
    need = np.array([0.0, 1_000.0, 5_000.0])
    w = welfare_weights(need)
    assert w[2] > w[1] > w[0] > 0
    assert np.allclose(welfare_weights(need, aversion=0), 1.1)


def test_conformal_adjustment_restores_coverage():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 2_000)
    lo, hi = np.full_like(y, -0.5), np.full_like(y, 0.5)   # far too narrow for N(0,1)
    q = conformal_adjustment(lo, hi, y, target_coverage=0.8)
    assert q > 0
    assert 0.79 <= np.mean((y >= lo - q) & (y <= hi + q)) <= 0.82


def test_cross_conformal_gives_honest_coverage_near_target():
    rng = np.random.default_rng(1)
    n = 3_000
    y = rng.normal(0, 1, n)
    oof = pd.DataFrame({"need_lo": -0.3, "need_mid": 0.0, "need_hi": 0.3, "fold": rng.integers(0, 5, n)})
    q, honest = cross_conformal(oof, y, np.ones(n, dtype=bool))
    assert q > 0 and 0.77 <= honest <= 0.83


class _Linear:
    """Minimal model obeying the interface: need = mean of labelled y + x."""
    kind, in_target_units = "test", True

    def fit(self, features, y):
        self.c = float(np.mean(y))
        return self

    def predict(self, features):
        mid = self.c + features["x"].values
        return pd.DataFrame({"need_lo": mid - 1, "need_mid": mid, "need_hi": mid + 1}, index=features.index)


def test_out_of_fold_predicts_every_row_and_never_trains_on_its_own_area():
    n = 400
    groups = np.repeat(np.arange(8), n // 8)
    features = pd.DataFrame({"x": np.zeros(n)})
    y = groups.astype(float) * 10          # each area has its own level
    labelled = np.ones(n, dtype=bool)
    labelled[::2] = False                   # unlabelled rows still get predictions

    oof = out_of_fold_predict(_Linear, features, y, labelled, groups)

    assert not oof[["need_lo", "need_mid", "need_hi"]].isna().any().any()
    assert set(oof["fold"]) == set(range(5))
    for g in range(8):   # an area's prediction is the mean of OTHER areas' labelled y
        pred = oof.loc[groups == g, "need_mid"].iloc[0]
        assert pred != pytest.approx(g * 10)


def _toy(n=800, seed=0):
    rng = np.random.default_rng(seed)
    f = pd.DataFrame({
        "monthly_income": rng.uniform(5_000, 80_000, n),
        "essential_costs": rng.uniform(20_000, 90_000, n),
        "food_security_score": rng.integers(0, 9, n),
        "shock_job_loss_12m": rng.random(n) < 0.2,
        "asset_phone": rng.random(n) < 0.6,
        "asset_tv": rng.random(n) < 0.3,
        "need_category": rng.choice(["food", "medical", "education"], n),
    })
    f["monthly_deficit"] = f["essential_costs"] - f["monthly_income"]
    f["deficit_ratio"] = f["monthly_deficit"] / f["essential_costs"]
    y = np.clip(f["monthly_deficit"] / 10 + 300 * f["food_security_score"] + rng.normal(0, 500, n), 0, None)
    return f, y


def test_lgbm_round_trips_through_its_artifact(tmp_path):
    f, y = _toy()
    m = LGBMNeedModel(params={"n_estimators": 50})
    m.fit(f, y)
    m.conformal = 123.0
    save(m, "lgbm-test", {"target": "poverty_gap"}, {"gates": {"passed": True}}, models_dir=tmp_path)

    loaded = load("lgbm_quantile", tmp_path / "lgbm-test")

    pd.testing.assert_frame_equal(m.predict(f), loaded.predict(f))
    assert loaded.conformal == 123.0
    assert loaded.explain(f.iloc[:2])[0][0][0] in loaded.feature_names


def test_single_application_scores_as_it_does_in_a_batch():
    f, y = _toy()
    m = LGBMNeedModel(params={"n_estimators": 50}).fit(f, y)
    one = m.predict(f.iloc[[7]])
    batch = m.predict(f)
    np.testing.assert_allclose(one.values, batch.iloc[[7]].values)


def test_central_estimate_never_rises_with_income():
    f, y = _toy()
    m = LGBMNeedModel(params={"n_estimators": 100}).fit(f, y)
    richer = f.copy()
    richer["monthly_income"] += 10_000
    richer["monthly_deficit"] = richer["essential_costs"] - richer["monthly_income"]
    richer["deficit_ratio"] = richer["monthly_deficit"] / richer["essential_costs"]
    assert (m.predict(richer)["need_mid"] <= m.predict(f)["need_mid"] + 1e-9).all()


def test_scoring_survives_fields_left_empty_for_a_whole_batch():
    """Data entered through the API can leave a field blank for every row
    scored; the model must still see the training-time column layout."""
    f, y = _toy()
    m = LGBMNeedModel(params={"n_estimators": 50}).fit(f, y)
    blank = f.iloc[:3].copy()
    blank["shock_job_loss_12m"] = None     # boolean in training
    blank["need_category"] = None          # categorical in training
    blank["asset_tv"] = None
    pred = m.predict(blank)
    assert pred.notna().all().all()
