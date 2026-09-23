import numpy as np
import pandas as pd

from backend.ml.audit import audit, exclusion_error


def test_exclusion_error_counts_only_deferrals_in_the_bottom_decile():
    welfare = np.arange(100, dtype=float)          # rows 0-9 are the bottom decile
    bands = np.array(["auto_approve"] * 100, dtype=object)
    bands[[0, 1, 2]] = "defer"
    bands[3] = "audit_approve"                      # approved at random: not excluded
    bands[50] = "defer"                             # not bottom decile: ignored
    assert exclusion_error(bands, welfare) == 0.3


def _decisions(n=200):
    rng = np.random.default_rng(1)
    return pd.DataFrame({
        "application_id": np.arange(n),
        "household_id": [f"h{i}" for i in range(n)],
        "band": "auto_approve",
        "need_category": rng.choice(["food", "medical"], n),
    })


def test_audit_reports_every_dimension_and_suppresses_small_groups():
    d = _decisions()
    protected = pd.DataFrame({"household_id": d["household_id"],
                              "gender_head": ["female"] * 190 + ["male"] * 10})
    welfare = np.arange(len(d), dtype=float)[::-1]   # last 20 rows are the bottom decile
    d.loc[d.index[-5:], "band"] = "defer"

    res = audit(d, protected, welfare, cycle_id=None, min_group_n=5)

    assert set(res["attribute"]) == {"gender_head", "need_category"}
    assert res["cycle_id"].isna().all()
    male = res[(res["attribute"] == "gender_head") & (res["group"] == "male")].iloc[0]
    assert male["n"] == 10 and male["exclusion_error"] == 0.5
    assert male["gap_vs_best"] == 0.5          # female: 10 in the bottom decile, none deferred

    assert audit(d, protected, welfare, cycle_id=3, min_group_n=11).query("attribute == 'gender_head'").empty


def test_truth_is_aligned_by_position_not_index():
    d = _decisions(20).set_index(np.arange(100, 120))    # non-default index, as after groupby
    d.loc[d.index[0], "band"] = "defer"
    protected = pd.DataFrame({"household_id": d["household_id"], "gender_head": "female"})
    welfare = pd.Series(np.arange(20, dtype=float), index=np.arange(500, 520))

    res = audit(d, protected, welfare, cycle_id=1, min_group_n=1)
    assert res.query("attribute == 'gender_head'")["exclusion_error"].iloc[0] == 0.5
