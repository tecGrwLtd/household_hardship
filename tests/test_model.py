import numpy as np
import pandas as pd

from backend.ml.model import conformal_adjustment, out_of_fold_predict, welfare_weights


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


def test_out_of_fold_predicts_every_row_including_unlabelled():
    rng = np.random.default_rng(0)
    n = 600
    X = pd.DataFrame({"x1": rng.normal(size=n), "x2": rng.normal(size=n)})
    y = pd.Series(3 * X["x1"] + rng.normal(0, 0.1, n))
    labelled = rng.random(n) < 0.5
    groups = pd.Series(rng.integers(0, 10, n))

    oof = out_of_fold_predict(X, y, labelled, groups)

    assert not oof.isna().any().any()
    assert np.corrcoef(oof["need_mid"], y)[0, 1] > 0.9
