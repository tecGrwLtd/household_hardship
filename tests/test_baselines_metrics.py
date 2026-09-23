import numpy as np
import pandas as pd
import pytest

from backend.ml.baselines import DeficitRankModel, RidgePMTModel, RuleBasedModel
from backend.ml.evaluate import gates
from backend.ml.features import AssetIndex
from backend.ml.metrics import direction_violations, inclusion_error, spearman, weighted_pinball
from tests.test_model import _toy


def test_rule_based_placeholder_is_transparent_and_ranks_by_hardship():
    f = pd.DataFrame({
        "monthly_deficit": [60_000.0, 0.0], "household_size": [3, 3], "shock_count_12m": [2, 0],
        "food_security_score": [7, 1], "dependency_ratio": [0.5, 0.0],
        "asset_phone": [False, True], "asset_tv": [False, True],
    })
    m = RuleBasedModel()
    pred = m.predict(f)
    assert pred.loc[0, "need_mid"] == 20_000 + 3_000 + 7_000 + 1_000
    assert pred.loc[0, "need_mid"] > pred.loc[1, "need_mid"]
    assert (pred["need_lo"] < pred["need_mid"]).iloc[0] and (pred["need_hi"] > pred["need_mid"]).iloc[0]
    terms = dict(m.explain(f)[0])
    assert terms["deficit_per_person"] == 20_000


def test_ridge_and_deficit_baselines_predict_every_row():
    f, y = _toy()
    for model in (RidgePMTModel(), DeficitRankModel()):
        pred = model.fit(f, y).predict(f)
        assert len(pred) == len(f) and not pred.isna().any().any()
        assert spearman(y, pred["need_mid"]) > 0.5


def test_asset_index_is_fixed_at_fit_time_and_oriented():
    train = pd.DataFrame({"asset_phone": [True, True, False, False], "asset_tv": [True, False, False, False]})
    ai = AssetIndex().fit(train)
    assert ai.transform(train.iloc[:1])[0] == ai.transform(train)[0]        # one row == in batch
    assert ai.transform(train)[0] > ai.transform(train)[3]                  # owns more -> higher
    assert AssetIndex(**ai.to_dict()).transform(train).tolist() == ai.transform(train).tolist()


def test_inclusion_error_counts_committed_approvals_above_the_line():
    bands = ["auto_approve", "audit_approve", "human_review", "defer", "auto_approve"]
    gap = [500.0, 0.0, 0.0, 0.0, 0.0]
    assert inclusion_error(bands, gap) == pytest.approx(2 / 3)


def test_weighted_pinball_is_zero_for_perfect_predictions_and_weights_the_needy():
    y = np.array([0.0, 100.0, 1_000.0])
    assert weighted_pinball(y, y) == 0
    miss_needy = weighted_pinball(y, y - np.array([0, 0, 100]))
    miss_other = weighted_pinball(y, y - np.array([100, 0, 0]))
    assert miss_needy > miss_other


class _Backwards:
    """Need rises with income — exactly what the direction check must catch."""
    def predict(self, f):
        mid = f["monthly_income"].astype(float).values
        return pd.DataFrame({"need_lo": mid, "need_mid": mid, "need_hi": mid}, index=f.index)


def test_direction_check_flags_a_reversed_effect():
    f = pd.DataFrame({
        "monthly_income": np.linspace(1_000, 50_000, 50), "essential_costs": 30_000.0, "amount_requested": 1.0,
        "household_size": 3, "rooms": 2, "income_std_12m": 100.0, "children_under_5": 0, "members_over_65": 0,
    })
    out = direction_violations(_Backwards(), f, {"monthly_income": -1}, tolerance=1.0)
    assert out["monthly_income"] == 1.0


def test_gates_require_beating_every_baseline():
    base = {"exclusion_error_bottom_decile": 0.02, "spearman_poor": 0.9, "weighted_pinball_mid": 300}
    good = {"exclusion_error_bottom_decile": 0.0, "spearman_poor": 0.95, "weighted_pinball_mid": 200,
            "coverage": 0.80, "direction_violations": {"monthly_income": 0.0}}
    models = {"lgbm": good, "ridge_pmt": base, "deficit_rank": {**base, "weighted_pinball_mid": np.nan}}
    assert gates(models, None)["passed"]
    assert not gates({**models, "lgbm": {**good, "coverage": 0.70}}, None)["passed"]
    assert not gates({**models, "lgbm": {**good, "direction_violations": {"monthly_income": 0.2}}}, None)["passed"]
