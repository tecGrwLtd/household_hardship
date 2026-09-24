"""The admin console: accounts, settings that change behaviour, the activity
log, system status, and the model showcase endpoints."""
from __future__ import annotations

import pytest

from tests.api.test_roles_filters import cw  # noqa: F401  (fixture)


def login(client, username, password):
    return client.post("/auth/login", data={"username": username, "password": password})


@pytest.mark.parametrize("path", ["/admin/users", "/admin/settings", "/admin/audit-log", "/admin/system", "/admin/caseworkers"])
def test_console_is_admin_only(client, cw, path):  # noqa: F811
    assert client.get(path, headers=cw).status_code == 403


def test_account_lifecycle(client, auth):
    assert client.post("/admin/users", headers=auth, json={
        "username": "mutesi", "display_name": "B. Mutesi", "password": "long-enough-pw", "role": "caseworker"}).status_code == 422
    r = client.post("/admin/users", headers=auth, json={
        "username": "mutesi", "display_name": "B. Mutesi", "password": "long-enough-pw", "role": "caseworker", "caseworker_id": 9})
    assert r.status_code == 201, r.text
    uid = r.json()["user_id"]
    assert login(client, "mutesi", "long-enough-pw").json()["user"]["caseworker_id"] == 9
    assert client.post("/admin/users", headers=auth, json={
        "username": "mutesi", "display_name": "x", "password": "long-enough-pw", "role": "admin"}).status_code == 409

    client.patch(f"/admin/users/{uid}", headers=auth, json={"password": "a-new-password"})
    assert login(client, "mutesi", "long-enough-pw").status_code == 401
    assert login(client, "mutesi", "a-new-password").status_code == 200
    client.patch(f"/admin/users/{uid}", headers=auth, json={"active": False})
    assert login(client, "mutesi", "a-new-password").status_code == 401

    me = client.get("/auth/me", headers=auth).json()
    assert client.patch(f"/admin/users/{me['user_id']}", headers=auth, json={"active": False}).status_code == 409


def test_change_own_password(client, cw):  # noqa: F811
    bad = client.post("/auth/password", headers=cw, json={"current_password": "nope", "new_password": "another-password"})
    assert bad.status_code == 403
    ok = client.post("/auth/password", headers=cw, json={"current_password": "caseworker-dev-only", "new_password": "caseworker-dev-only-2"})
    assert ok.status_code == 204
    client.post("/auth/password", headers=cw, json={"current_password": "caseworker-dev-only-2", "new_password": "caseworker-dev-only"})


def test_settings_are_validated_and_logged(client, auth):
    assert client.put("/admin/settings/random_audit_rate", headers=auth, json={"value": 0.06}).status_code == 422  # spec: 3-5%
    assert client.put("/admin/settings/nope", headers=auth, json={"value": 1}).status_code == 404
    r = client.put("/admin/settings/random_audit_rate", headers=auth, json={"value": 0.05})
    assert r.status_code == 200 and r.json()["value"] == 0.05 and r.json()["updated_by"] == "admin"
    client.put("/admin/settings/random_audit_rate", headers=auth, json={"value": 0.04})
    log = client.get("/admin/audit-log", headers=auth, params={"action": "setting."}).json()
    assert log["items"][0]["details"] == {"from": 0.05, "to": 0.04}


def test_launch_thresholds_block_activation(client, auth, sql):
    sql("""INSERT INTO model_versions (model_version, kind, purpose, status, metrics) VALUES
           ('lgbm-threshold-test', 'lgbm_quantile', 'need', 'candidate',
            '{"gates": {"passed": true, "max_subgroup_gap": 0.02}, "models": {"lgbm": {"exclusion_error_bottom_decile": 0.10}}}')
           ON CONFLICT DO NOTHING""")
    try:
        client.put("/admin/settings/max_exclusion_error", headers=auth, json={"value": 0.05})
        refused = client.post("/models/lgbm-threshold-test/activate", headers=auth)
        assert refused.status_code == 409 and "within_max_exclusion_error" in refused.text
        client.put("/admin/settings/max_exclusion_error", headers=auth, json={"value": None})
        assert client.post("/models/lgbm-threshold-test/activate", headers=auth).status_code == 200
        entry = client.get("/admin/audit-log", headers=auth, params={"action": "model.activate"}).json()["items"][0]
        assert entry["target"] == "lgbm-threshold-test"
    finally:
        client.put("/admin/settings/max_exclusion_error", headers=auth, json={"value": None})
        client.post("/models/rules-v0/activate", headers=auth)


def test_system_status_and_showcase(client, auth):
    s = client.get("/admin/system", headers=auth).json()
    assert s["counts"]["applications"] > 7000 and s["security"]["development_secret"] is True
    card = client.get("/models/rules-v0/card", headers=auth).json()
    assert card["terms"] and card["version"]["kind"] == "rules"
    r = client.post("/models/what-if", headers=auth, json={
        "area_code": "AR007", "need_category": "food", "amount_requested": 20000, "household_size": 5,
        "monthly_income": 10000, "essential_costs": 80000, "food_security_score": 7, "shocks": ["job_loss"]})
    assert r.status_code == 200, r.text
    assert r.json()["need_lo"] <= r.json()["need_mid"] <= r.json()["need_hi"] and r.json()["drivers"]
