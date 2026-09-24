"""Roles (admin vs caseworker) and the sidebar's global filters."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def cw(client):
    r = client.post("/auth/login", data={"username": "uwase", "password": "caseworker-dev-only"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "caseworker" and r.json()["user"]["caseworker_id"] == 1
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_me_reports_the_role(client, auth, cw):
    assert client.get("/auth/me", headers=auth).json()["role"] == "admin"
    assert client.get("/auth/me", headers=cw).json() == {
        "user_id": 1, "username": "uwase", "display_name": "J. Uwase", "role": "caseworker", "caseworker_id": 1}


@pytest.mark.parametrize("method,path", [
    ("get", "/models"), ("post", "/models/rules-v0/activate"), ("get", "/cycles/1/preview"),
    ("post", "/cycles/1/allocate"), ("get", "/dashboard/fairness"), ("get", "/dashboard/model-health"),
    ("get", "/dashboard/drift"), ("get", "/dashboard/override-trend"),
])
def test_programme_management_is_admin_only(client, cw, method, path):
    assert getattr(client, method)(path, headers=cw).status_code == 403


def test_caseworker_cannot_create_cycles(client, cw):
    r = client.post("/cycles", headers=cw, json={"period_start": "2040-01-01", "period_end": "2040-01-28", "budget_total": 1})
    assert r.status_code == 403


def test_caseworker_work_is_attributed_to_them(client, auth, cw, sql):
    from tests.api.test_workflow import new_cycle, new_household, submit
    cycle_id, start = new_cycle(client, auth, budget=1_000_000)
    hid = new_household(client, cw, income=15_000)
    r = client.post("/applications", headers=cw, json={
        "household_id": hid, "cycle_id": cycle_id, "caseworker_id": 5, "amount_requested": 9_000,
        "need_category": "food", "submitted_at": f"{start}T10:00:00Z"})
    assert r.status_code == 201 and r.json()["caseworker_id"] == 1     # not the 5 it asked for
    app_id = r.json()["application_id"]
    client.post(f"/cycles/{cycle_id}/allocate", headers=auth)
    sql("UPDATE applications SET status = 'in_review' WHERE application_id = %s", (app_id,))

    queue = client.get("/reviews/queue", headers=cw).json()
    assert queue["mine"] is True and all(i["caseworker_id"] == 1 for i in queue["items"])
    # the tab counts describe the whole queue, not just the caseworker's slice
    assert queue["counts"]["all"] > queue["counts"]["mine"] == len(queue["items"])
    assert app_id in {i["application_id"] for i in queue["items"]}
    assert client.post(f"/reviews/{app_id}", headers=cw, json={"decision": "deny"}).status_code == 422   # no reason
    review = client.post(f"/reviews/{app_id}", headers=cw,
                         json={"decision": "deny", "caseworker_id": 5, "notes": "checked with family"}).json()["review"]
    assert review["caseworker_id"] == 1

    home = client.get("/me/summary", headers=cw).json()
    assert home["user"]["display_name"] == "J. Uwase" and home["reviews"]["reviews"] >= 1


def test_filter_options(client, auth):
    o = client.get("/dashboard/filters", headers=auth).json()
    assert o["support_groups"] == ["health", "food", "housing_bills", "education_childcare", "funeral_other"]
    assert set(o["regions"]) == {"Kigali", "Northern", "Southern", "Eastern", "Western"}
    assert len(o["districts"]) == 30 and o["months"][0] >= o["months"][-1]


def test_filters_narrow_every_dashboard_figure(client, auth):
    month = {"month": "2026-08-01"}
    all_ = client.get("/dashboard/summary", headers=auth, params=month).json()
    kigali = client.get("/dashboard/summary", headers=auth, params={**month, "region": "Kigali"}).json()
    health = client.get("/dashboard/summary", headers=auth, params={**month, "support_group": "health"}).json()

    assert all_["applicants"] == 471 and all_["budget_applies"] and all_["previous_applicants"] == 476
    assert 0 < kigali["applicants"] < all_["applicants"] and not kigali["budget_applies"]
    assert health["applicants"] == 105

    types = client.get("/dashboard/support-types", headers=auth, params=month).json()
    assert sum(t["applicants"] for t in types) == 471
    outcomes = client.get("/dashboard/outcomes", headers=auth, params=month).json()
    assert sum(o["applications"] for o in outcomes) == 471
    districts = client.get("/dashboard/districts", headers=auth, params={**month, "region": "Kigali"}).json()
    assert {d["region"] for d in districts} == {"Kigali"} and sum(d["applicants"] for d in districts) == kigali["applicants"]
    trend = client.get("/dashboard/monthly", headers=auth, params={**month, "months": 3}).json()
    assert {str(r["month"]) for r in trend} == {"2026-06-01", "2026-07-01", "2026-08-01"}


def test_application_list_pages_and_counts(client, auth):
    page = client.get("/applications", headers=auth, params={"month": "2026-08-01", "limit": 10}).json()
    assert page["total"] == 471 and len(page["items"]) == 10
    assert sum(page["status_counts"].values()) == 471
    deferred = client.get("/applications", headers=auth, params={"month": "2026-08-01", "status": "deferred", "limit": 5}).json()
    assert deferred["total"] == page["status_counts"]["deferred"] and {i["status"] for i in deferred["items"]} == {"deferred"}
    one = page["items"][0]
    detail = client.get(f"/applications/{one['application_id']}", headers=auth).json()
    assert detail["survey"] is not None and detail["history"] and detail["area_name"] == one["area_name"]


def test_household_search(client, auth):
    r = client.get("/households", headers=auth, params={"q": "Huye", "limit": 5}).json()
    assert r["total"] > 0 and all(h["area_name"] == "Huye" for h in r["items"])
