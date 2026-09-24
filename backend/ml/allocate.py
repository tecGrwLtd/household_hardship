"""Deterministic allocation layer — turns need predictions into decisions
under a fixed budget. Contains NO learned parameters, on purpose: the
ethical choices (auto-approval is the safe direction, the random audit
sample is written into the function itself rather than left as an optional
optimisation) live in code you can read, not in model weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import RANDOM_AUDIT_RATE

# Bands whose cost is committed as soon as allocation runs. human_review is
# not: a reviewer decides, spending from whatever budget is left.
COMMITTED_BANDS = ("auto_approve", "audit_approve")


def allocate(scores: pd.DataFrame, budget: float, rng: np.random.Generator,
             audit_rate: float = RANDOM_AUDIT_RATE) -> tuple[pd.DataFrame, float]:
    """scores: application_id, household_id, need_lo, need_mid, need_hi, amount_requested.
    Returns (ranked_with_band, cutoff).

    Band logic:
      - lo > cutoff             -> auto_approve  (safe direction: wrong here just
                                                    costs a bit of budget headroom)
      - hi < cutoff             -> defer          (the adverse decision — this is
                                                    where GDPR Art.22 / human review bites)
      - interval straddles cutoff -> human_review

    cutoff is the need_mid of the highest-ranked application the budget
    cannot cover. When the budget covers every application there is no such
    applicant: cutoff is -inf and everyone is auto-approved — deferring
    anyone in a cycle with money to spare would be an adverse decision with
    no justification.

    Auto-approval is a spending commitment, so its total never exceeds the
    budget: if quantile crossing (lo above another applicant's mid) would
    push it over, the lowest-ranked auto-approvals drop to human_review.

    A random `audit_rate` share (default RANDOM_AUDIT_RATE; the platform
    setting, kept within the spec's 3-5%) of the deferred group is flipped to
    audit_approve — the only source of unbiased future labels for retraining.
    This line is not optional and must not be turned off as a cost-saving
    measure; see the design spec's selective-labels section. Its cost comes
    on top of the ranked allocation; budget_summary() reports it.
    """
    ranked = scores.sort_values("need_mid", ascending=False).reset_index(drop=True).copy()
    ranked["cum_cost"] = ranked["amount_requested"].cumsum()

    n_funded = int((ranked["cum_cost"] <= budget).sum())
    if n_funded >= len(ranked):
        ranked["band"] = "auto_approve"
        return ranked, float("-inf")

    cutoff = float(ranked["need_mid"].iloc[n_funded])
    ranked["band"] = np.select(
        [ranked["need_lo"] > cutoff, ranked["need_hi"] < cutoff],
        ["auto_approve", "defer"],
        default="human_review",
    )

    auto = ranked["band"] == "auto_approve"
    over_budget = ranked.loc[auto, "amount_requested"].cumsum() > budget
    ranked.loc[over_budget[over_budget].index, "band"] = "human_review"

    deferred_idx = ranked.index[ranked["band"] == "defer"]
    n_audit = int(round(len(deferred_idx) * audit_rate))
    if n_audit:
        picked = rng.choice(deferred_idx, size=n_audit, replace=False)
        ranked.loc[picked, "band"] = "audit_approve"

    return ranked, cutoff


def budget_summary(ranked: pd.DataFrame, budget: float) -> dict[str, float]:
    """How a cycle's budget splits after allocate(): what is already
    committed (auto + audit approvals), what sits with reviewers, and what is
    left for them to award. `remaining` can go negative only through the
    audit sample, which is mandatory by design."""
    cost = ranked.groupby("band")["amount_requested"].sum()
    committed = float(sum(cost.get(b, 0.0) for b in COMMITTED_BANDS))
    return {
        "budget": float(budget),
        "committed": committed,
        "in_review": float(cost.get("human_review", 0.0)),
        "remaining": float(budget) - committed,
    }


SCORE_COLUMNS = ["application_id", "household_id", "need_lo", "need_mid", "need_hi", "amount_requested"]


def allocate_all_cycles(scored: pd.DataFrame, funding_cycles: pd.DataFrame,
                        rng: np.random.Generator) -> tuple[pd.DataFrame, list[dict]]:
    """Run allocate() per cycle under that cycle's budget. `scored` needs
    SCORE_COLUMNS + cycle_id; any extra columns ride along onto the result.
    Returns (all cycles' ranked rows with band + cutoff, per-cycle budget_summary)."""
    budgets = funding_cycles.set_index("cycle_id")["budget_total"].astype(float)
    frames, summaries = [], []
    for cycle_id, cycle_df in scored.groupby("cycle_id"):
        if cycle_id not in budgets.index:
            continue
        ranked, cutoff = allocate(cycle_df[SCORE_COLUMNS], budgets[cycle_id], rng)
        extra = cycle_df.drop(columns=SCORE_COLUMNS[1:]).set_index("application_id")
        ranked = ranked.join(extra, on="application_id")
        ranked["cutoff"] = cutoff
        frames.append(ranked)
        summaries.append({"cycle_id": cycle_id, **budget_summary(ranked, budgets[cycle_id])})
    if not frames:
        return pd.DataFrame(columns=[*SCORE_COLUMNS, "cycle_id", "band", "cutoff"]), summaries
    return pd.concat(frames, ignore_index=True), summaries
