import numpy as np

from backend.ml.model import welfare_weights


def test_welfare_weights_favour_the_worst_off():
    # target is poverty_gap: higher = worse off = heavier weight
    need = np.array([0.0, 1_000.0, 5_000.0])
    w = welfare_weights(need)
    assert w[2] > w[1] > w[0] > 0
    assert np.allclose(welfare_weights(need, aversion=0), 1.1)

