"""The platform's main path, end to end through the API: register
households, survey them, open a cycle, submit applications, preview and
allocate, review, appeal — plus the guard rails around each step."""
from __future__ import annotations

import itertools

import pytest

_period = itertools.count()


def new_cycle(client, auth, budget: float) -> int:
    """A fresh cycle in its own future month, so tests never share one."""
    k = next(_period)
    year, month = 2030 + k // 12, k % 12 + 1
    r = client.post("/cycles", headers=auth, json={
        "period_start": f"{year}-{month:02d}-01", "period_end": f"{year}-{month:02d}-28", "budget_total": budget})
    assert r.status_code == 201, r.text
    c = r.json()
    return c["cycle_id"], c["period_start"]


def new_household(client, auth, income: float, costs: float = 90_000, shocks: bool = False) -> str:
    r = client.post("/households", headers=auth, json={"area_code": "AR007"})
    assert r.status_code == 201, r.text
    hid = r.json()["household_id"]
    survey = {"survey_date": "2029-12-01", "household_size": 5, "children_under_5": 1, "rooms": 2,
              "monthly_income": income, "income_std_12m": income * 0.3, "essential_costs": costs,
              "food_security_score": 6 if shocks else 2, "shock_job_loss_12m": shocks,
              "employment_type": "informal", "tenure": "informal", "education_head": "primary"}
    r = client.post(f"/households/{hid}/surveys", headers=auth, json=survey)
    assert r.status_code == 201, r.text
    return hid


def submit(client, auth, hid: str, cycle_id: int, day: str, amount: float = 20_000, **extra) -> dict:
    r = client.post("/applications", headers=auth, json={
        "household_id": hid, "cycle_id": cycle_id, "caseworker_id": 1, "amount_requested": amount,
        "need_category": "food", "submitted_at": f"{day}T10:00:00Z", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_login_is_required(client):
    assert client.get("/areas").status_code == 401
    assert client.post("/auth/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_submit_derives_history_and_returns_a_provisional_score(client, auth):
    cycle_id, start = new_cycle(client, auth, 1_000_000)
    hid = new_household(client, auth, income=20_000, shocks=True)

    first = submit(client, auth, hid, cycle_id, start)
    second = submit(client, auth, hid, cycle_id, start[:8] + "20", referral_source="self")

    assert first["prior_applications_count"] == 0 and first["days_since_last_application"] is None
    assert second["prior_applications_count"] == 1 and second["days_since_last_application"] == 19
    assert second["application_completeness"] == 0.2    # 1 of 5 optional fields given
    p = first["provisional_score"]
    assert p["need_lo"] <= p["need_mid"] <= p["need_hi"] and p["has_survey"] and p["top_drivers"]


def test_submission_is_validated(client, auth):
    cycle_id, start = new_cycle(client, auth, 1_000_000)
    hid = new_household(client, auth, income=30_000)
    bad_date = client.post("/applications", headers=auth, json={
        "household_id": hid, "cycle_id": cycle_id, "amount_requested": 1, "need_category": "food",
        "submitted_at": "2001-01-01T00:00:00Z"})
    assert bad_date.status_code == 422 and "outside cycle" in bad_date.text
    unknown_field = client.post("/applications", headers=auth, json={
        "household_id": hid, "cycle_id": cycle_id, "amount_requested": 1, "need_category": "food",
        "prior_applications_count": 0})
    assert unknown_field.status_code == 422      # history is derived server-side, never accepted
    assert client.post("/households", headers=auth, json={"area_code": "NOPE"}).status_code == 422


def test_preview_writes_nothing_and_allocate_commits_within_budget(client, auth):
    cycle_id, start = new_cycle(client, auth, budget=60_000)
    apps = [submit(client, auth, new_household(client, auth, income=inc, shocks=inc < 20_000), cycle_id, start)
            for inc in (5_000, 10_000, 15_000, 40_000, 70_000, 110_000, 150_000)]

    preview = client.get(f"/cycles/{cycle_id}/preview", headers=auth).json()
    assert len(preview["applications"]) == 7
    statuses = {client.get(f"/applications/{a['application_id']}", headers=auth).json()["status"] for a in apps}
    assert statuses == {"submitted"}

    result = client.post(f"/cycles/{cycle_id}/allocate", headers=auth).json()
    bands = {a["application_id"]: a["band"] for a in result["applications"]}
    assert set(bands.values()) <= {"auto_approve", "human_review", "defer", "audit_approve"}
    budget = client.get(f"/cycles/{cycle_id}/budget", headers=auth).json()
    assert budget["awarded"] == result["summary"]["committed"] <= 60_000 or "audit_approve" in bands.values()

    for app_id, band in bands.items():
        detail = client.get(f"/applications/{app_id}", headers=auth).json()
        assert detail["score"]["band"] == band and detail["provisional_score"] is None
        assert (detail["award"] is not None) == (band in ("auto_approve", "audit_approve"))

    # re-running only touches newly submitted applications
    again = client.post(f"/cycles/{cycle_id}/allocate", headers=auth).json()
    assert again["applications"] == []


def test_review_approve_deny_appeal_close(client, auth, sql):
    cycle_id, start = new_cycle(client, auth, budget=1_000_000)
    ids = [submit(client, auth, new_household(client, auth, income=inc), cycle_id, start)["application_id"]
           for inc in (10_000, 20_000)]
    client.post(f"/cycles/{cycle_id}/allocate", headers=auth)
    # Force both into review so the path is deterministic regardless of the model's bands.
    sql("UPDATE applications SET status = 'in_review' WHERE application_id = ANY(%s)", (ids,))

    queue = client.get("/reviews/queue", headers=auth, params={"cycle_id": cycle_id}).json()["items"]
    assert {q["application_id"] for q in queue} == set(ids)
    assert all(q["model_lean"] in ("approve", "deny") for q in queue)

    approved = client.post(f"/reviews/{ids[0]}", headers=auth, json={"decision": "approve", "notes": "ok"}).json()
    assert approved["status"] == "awarded"
    assert client.get(f"/applications/{ids[0]}", headers=auth).json()["award"] is not None

    denied = client.post(f"/reviews/{ids[1]}", headers=auth, json={"decision": "deny", "notes": "not enough"}).json()
    assert denied["status"] == "deferred" and denied["review"]["final_decision"] == "denied"
    assert client.post(f"/reviews/{ids[1]}", headers=auth, json={"decision": "deny", "notes": "again"}).status_code == 409

    assert client.post(f"/applications/{ids[1]}/appeal", headers=auth).json()["status"] == "appealed"
    assert client.post(f"/applications/{ids[0]}/appeal", headers=auth).status_code == 409   # awarded
    closed = client.post(f"/reviews/{ids[1]}", headers=auth, json={"decision": "deny", "notes": "appeal heard"}).json()
    assert closed["status"] == "closed" and closed["review"]["final_decision"] == "appeal_denied"


def test_approval_cannot_overspend_the_cycle(client, auth, sql):
    cycle_id, start = new_cycle(client, auth, budget=1_000)
    app_id = submit(client, auth, new_household(client, auth, income=5_000), cycle_id, start, amount=50_000)["application_id"]
    client.post(f"/cycles/{cycle_id}/allocate", headers=auth)
    sql("UPDATE applications SET status = 'in_review' WHERE application_id = %s", (app_id,))
    r = client.post(f"/reviews/{app_id}", headers=auth, json={"decision": "approve", "notes": "urgent"})
    assert r.status_code == 409 and "exceed the cycle budget" in r.text


def test_protected_attributes_are_write_only(client, auth):
    hid = new_household(client, auth, income=30_000)
    r = client.put(f"/households/{hid}/protected-attributes", headers=auth,
                   json={"ethnicity": "not_disclosed", "gender_head": "female", "age_band": "36-45"})
    assert r.status_code == 204
    body = client.get(f"/households/{hid}", headers=auth).json()
    assert "ethnicity" not in str(body) and "gender_head" not in body


def test_dashboard_endpoints(client, auth):
    support = client.get("/dashboard/monthly-support", headers=auth, params={"start": "2026-08-01", "end": "2026-08-01"})
    assert support.status_code == 200
    assert {r["support_group"] for r in support.json()} == {"education", "health", "financial", "bereavement"}
    for path in ("/dashboard/monthly-applications", "/dashboard/repeat-support", "/dashboard/cycles",
                 "/dashboard/model-health", "/dashboard/fairness", "/need-categories", "/cycles"):
        assert client.get(path, headers=auth).status_code == 200, path


def test_model_activation_is_gated(client, auth, sql):
    sql("""INSERT INTO model_versions (model_version, kind, status, metrics)
           VALUES ('rules-bad', 'lgbm_quantile', 'candidate', '{"gates": {"passed": false, "coverage_in_range": false}}')
           ON CONFLICT DO NOTHING""")
    assert client.post("/models/nope/activate", headers=auth).status_code == 404
    refused = client.post("/models/rules-bad/activate", headers=auth)
    assert refused.status_code == 409 and "coverage_in_range" in refused.text
    assert client.post("/models/rules-bad/activate", headers=auth, json={"force": True}).status_code == 422

    # rules-v0 needs no gates; switching back and forth retires the other
    assert client.post("/models/rules-v0/activate", headers=auth).json()["active"] == "rules-v0"
    assert client.get("/models/active", headers=auth).json()["model_version"] == "rules-v0"


def test_allocation_with_a_trained_model(client, auth, sql):
    """Same path with the trained LightGBM artifact, if one has been built
    (`python -m backend.ml train`); skipped otherwise."""
    from backend.ml.registry import MODELS_DIR
    trained = sorted(p for p in MODELS_DIR.glob("lgbm-*") if (p / "metadata.json").exists())
    if not trained:
        pytest.skip("no trained model in models/ — run `python -m backend.ml train`")
    version = trained[-1].name
    sql("""INSERT INTO model_versions (model_version, kind, status, artifact_path, metrics)
           VALUES (%s, 'lgbm_quantile', 'candidate', %s, '{"gates": {"passed": true}}')
           ON CONFLICT DO NOTHING""", (version, f"models/{version}"))
    assert client.post(f"/models/{version}/activate", headers=auth).status_code == 200
    try:
        cycle_id, start = new_cycle(client, auth, budget=80_000)
        for inc in (5_000, 30_000, 120_000):
            submit(client, auth, new_household(client, auth, income=inc, shocks=inc < 10_000), cycle_id, start)
        result = client.post(f"/cycles/{cycle_id}/allocate", headers=auth).json()
        assert result["model_version"] == version
        mids = {a["need_mid"] for a in result["applications"]}
        assert len(mids) == 3
        poorest = max(result["applications"], key=lambda a: a["need_mid"])
        assert poorest["top_drivers"][0]["feature"] in (trained[-1] / "metadata.json").read_text()
    finally:
        client.post("/models/rules-v0/activate", headers=auth)


def test_one_active_model_per_purpose_and_forecasts_follow_allocation(client, auth, sql, tmp_path):
    """Activating a repeat forecaster leaves the need model alone, and
    allocation / review refresh the planning forecasts."""
    from backend.ml.registry import save
    from backend.ml.repeat import HistoryRepeatModel
    import pandas as pd
    hist = pd.DataFrame({"prior_applications_count": [0, 1, 2, 3] * 25,
                         "days_since_last_application": [None, 100, 200, 400] * 25,
                         "status": ["awarded", "deferred"] * 50})
    model = HistoryRepeatModel().fit(hist, [0, 1, 1, 1] * 25)
    path = save(model, "repeat-apitest", {}, {"gates": {"passed": True}}, models_dir=tmp_path)
    sql("""INSERT INTO model_versions (model_version, kind, purpose, status, artifact_path, metrics)
           VALUES ('repeat-apitest', 'repeat_history', 'repeat', 'candidate', %s, '{"gates": {"passed": true}}')""",
        (str(path),))
    need_before = client.get("/models/active", headers=auth).json()["model_version"]
    assert client.post("/models/repeat-apitest/activate", headers=auth).json()["retired"] is None
    assert client.get("/models/active", headers=auth).json()["model_version"] == need_before
    assert client.get("/models/active", headers=auth, params={"purpose": "repeat"}).json()["model_version"] == "repeat-apitest"
    assert client.get("/health").json()["active_repeat_model"] == "repeat-apitest"

    cycle_id, start = new_cycle(client, auth, budget=500_000)
    app_id = submit(client, auth, new_household(client, auth, income=10_000), cycle_id, start)["application_id"]
    client.post(f"/cycles/{cycle_id}/allocate", headers=auth)
    assert sql("SELECT count(*) FROM repeat_forecasts WHERE application_id = %s", (app_id,))[0][0] == 1

    rows = client.get("/dashboard/repeat-forecast", headers=auth, params={"start": start, "end": start}).json()
    assert sum(r["forecast_count"] for r in rows) >= 1 and all(r["observable_count"] == 0 for r in rows)


def test_monitoring_endpoints(client, auth):
    drift = client.get("/dashboard/drift", headers=auth).json()
    assert "latest" in drift and "history" in drift
    trend = client.get("/dashboard/override-trend", headers=auth).json()
    assert trend and {"month", "reviews", "override_rate", "override_rate_ok"} <= set(trend[0])
