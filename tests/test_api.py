"""The HTTP surface.

The contract worth testing is not that it returns a number â€” it is that it
cannot return a number *without* the things that say how much to trust it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_path, monkeypatch):
    """Point the API at the session corpus.

    Deliberately NOT by reloading xycalc.build: reload re-executes the module
    body, resetting DATA and LOCAL out from under the monkeypatches that other
    test modules rely on, and the resulting failures land in whichever file
    runs next rather than in this one.
    """
    import xycalc.db as db_mod
    from xycalc.api import app

    monkeypatch.setattr(db_mod, "DEFAULT_DB", db_path)
    return TestClient(app)


SIZING = {
    "model": "mongodb.wt-cache",
    "inputs": {"storage_size": "500GB", "index_size": "40GB"},
}


class TestModels:
    def test_lists_models_with_their_inputs(self, client):
        body = client.get("/api/models").json()
        slugs = {m["slug"] for m in body["models"]}
        assert "mongodb.wt-cache" in slugs

    def test_every_listed_model_carries_its_validation_status(self, client):
        """So a caller building a picker cannot show a model without showing
        how much it has been checked."""
        for m in client.get("/api/models").json()["models"]:
            assert m["validation"]["text"]


class TestSizing:
    def test_returns_the_worked_answer(self, client):
        body = client.post("/api/sizing", json=SIZING).json()
        assert body["answer"]["mode"] == pytest.approx(1612.5 * 1000**3)

    def test_always_returns_a_band_not_a_point(self, client):
        a = client.post("/api/sizing", json=SIZING).json()["answer"]
        assert a["lo"] < a["mode"] < a["hi"]

    def test_validation_status_is_never_omitted(self, client):
        """A response without it would read as a validated answer to anything
        that forgot to check. Asserts presence and substance, not a particular
        verdict â€” the verdict moves as observations land, and a test pinned to
        `unvalidated` would have to be edited every time the corpus improves."""
        body = client.post("/api/sizing", json=SIZING).json()
        assert "validation" in body
        assert body["validation"]["text"]
        assert isinstance(body["validation"]["validated"], bool)

    def test_an_unchecked_model_still_says_so(self, client):
        body = client.get("/api/why/celery.worker-prefetch").json()
        assert body["validation"]["validated"] is False
        assert "unvalidated" in body["validation"]["text"]

    def test_every_computed_step_carries_its_citation(self, client):
        body = client.post("/api/sizing", json=SIZING).json()
        for step in body["steps"]:
            if step["skipped"] or step["coefficient"] is None:
                continue
            assert step["source"], f"{step['key']} has no source"
            assert step["applies_to"], f"{step['key']} names no versions"

    def test_constraints_come_back_even_though_they_do_not_compute(self, client):
        body = client.post("/api/sizing", json=SIZING).json()
        assert len(body["constraints"]) == 5

    def test_the_reframe_is_part_of_the_answer(self, client):
        """For this model the reframe is most of the answer, so a client that
        renders only `answer` is rendering the wrong thing â€” but it has to be
        able to get it."""
        body = client.post("/api/sizing", json=SIZING).json()
        assert "Avoid increasing" in body["reframe"]

    def test_headroom_is_returned_when_asked(self, client):
        body = client.post(
            "/api/sizing", json={**SIZING, "available": "4TB"}
        ).json()
        assert "covered" in body["headroom"]["verdict"]

    def test_get_hints_at_post_instead_of_demanding_underscore(self, client):
        """GET used to declare ``**_``, which FastAPI treated as a required
        query parameter named ``_`` and answered 422. The stub is a 400 that
        points at POST, including when a caller sends leftover query params."""
        r = client.get(
            "/api/sizing/mongodb.wt-cache",
            params={"storage_size": "500GB", "index_size": "40GB"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "use POST /api/sizing"

    def test_unknown_model_is_404(self, client):
        assert client.post("/api/sizing", json={"model": "nope"}).status_code == 404

    def test_missing_required_input_is_422(self, client):
        r = client.post("/api/sizing", json={"model": "mongodb.wt-cache", "inputs": {}})
        assert r.status_code == 422

    def test_unreadable_size_is_422_not_500(self, client):
        r = client.post(
            "/api/sizing",
            json={"model": "mongodb.wt-cache", "inputs": {"storage_size": "loads"}},
        )
        assert r.status_code == 422

    def test_malformed_numeric_size_is_422_not_500(self, client):
        r = client.post(
            "/api/sizing",
            json={"model": "mongodb.wt-cache", "inputs": {"storage_size": "1.2.3GB"}},
        )
        assert r.status_code == 422

    def test_unreadable_scalar_is_422_not_500(self, client):
        r = client.post(
            "/api/sizing",
            json={
                "model": "ebs.iops-to-provision",
                "inputs": {"average_iops": "abc"},
            },
        )
        assert r.status_code == 422

    def test_non_string_available_is_422_not_500(self, client):
        r = client.post(
            "/api/sizing", json={**SIZING, "available": ["4TB"]}
        )
        assert r.status_code == 422


class TestWhy:
    def test_returns_the_citation_chain_without_running_the_model(self, client):
        """Asserts the invariant, not a term count. A hardcoded 7 broke the
        moment the model gained its floor term, which told us nothing about
        whether the endpoint works."""
        body = client.get("/api/why/mongodb.wt-cache").json()
        assert len(body["terms"]) >= 7
        for term in body["terms"]:
            assert term["rationale"], f"{term['key']} explains nothing"
            assert term["coefficient"] or term["input_key"], (
                f"{term['key']} cites nothing and reads no input"
            )

    def test_documented_terms_expose_the_sentence_they_came_from(self, client):
        body = client.get("/api/why/mongodb.wt-cache").json()
        quoted = [t for t in body["terms"] if t["quote"]]
        assert len(quoted) >= 4

    def test_unknown_model_is_404(self, client):
        assert client.get("/api/why/nope").status_code == 404

    def test_sizing_sensitivity_is_opt_in(self, client):
        plain = client.post("/api/sizing", json=SIZING).json()
        assert "sensitivity" not in plain
        body = client.post(
            "/api/sizing", json={**SIZING, "sensitivity": True}
        ).json()
        assert body["sensitivity"]["measure_next"]["key"] == "decompression"
        assert "decompression" in body["sensitivity"]["sentence"]


class TestPage:
    def test_the_page_is_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "xycalc" in r.text

    def test_gui_serves_the_same_calculator_as_export(self, client):
        """GUI and static deploy share one template. The corpus is compiled into
        the page (with golden self-check); there is no second API-driven HTML.
        """
        html = client.get("/").text
        assert 'id="corpus"' in html
        assert "XY.checkGolden" in html
        assert 'data-tab="scenario"' in html
        assert 'data-tab="math"' in html
        assert 'id="mode-basic"' in html
        assert 'data-tab="single"' in html
        assert 'data-tab="flow"' in html
        assert 'data-tab="occupancy"' in html
        assert 'data-tab="cliff"' in html
        assert 'id="simple-view"' in html
        assert "occupancy_band" in html
        assert "cache_cliff" in html
        assert "mongodb.wt-cache" in html

    def test_api_corpus_matches_page_blob(self, client):
        page = client.get("/").text
        api = client.get("/api/corpus").json()
        assert api["corpus_digest"] in page
        assert api["occupancy_band"]["ladder"]["eviction_target"]["value"] == 80
        assert len(api["occupancy_band"]["passes"]) == 3
        assert len(api["occupancy_band"]["ticket_ladder"]) == 3
        assert len(api["occupancy_band"]["playbook"]) >= 4
        assert api["cache_cliff"]["status"] == "measured"
        assert len(api["cache_cliff"]["legs"]) == 9
        assert len(api["cache_cliff"]["a2_legs"]) == 6
