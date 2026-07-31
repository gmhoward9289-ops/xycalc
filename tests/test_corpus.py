"""Corpus hygiene — the rules that keep the citations meaningful rather than
merely present.

A source_id that is NOT NULL proves a number has a citation. It does not prove
the citation says what the number claims. These tests close the gap that
mechanism alone cannot.
"""

from __future__ import annotations

import pytest


def rows(conn, sql):
    return conn.execute(sql).fetchall()


class TestSources:
    def test_every_source_is_retrievable(self, conn):
        """A source nobody can go and read is an assertion, not a citation."""
        for r in rows(conn, "SELECT slug, url, source_type FROM source"):
            if r["source_type"] in ("estimate", "derived", "measured"):
                continue
            assert r["url"], f"{r['slug']} has no URL"

    def test_every_source_records_when_it_was_read(self, conn):
        for r in rows(conn, "SELECT slug, retrieved_on FROM source"):
            assert r["retrieved_on"], f"{r['slug']} has no retrieved_on"

    def test_every_source_explains_why_it_is_carried(self, conn):
        """Notes are where the reason a source is trusted lives — and, for the
        two that contradict each other, where that is recorded."""
        for r in rows(conn, "SELECT slug, notes FROM source"):
            assert r["notes"] and len(r["notes"]) > 40, f"{r['slug']} has no notes"


class TestCoefficients:
    def test_documented_figures_carry_the_sentence_they_came_from(self, conn):
        """The verbatim gate, applied to the corpus rather than to a research
        batch. A figure graded `documented` claims a source states it outright,
        and the quote is what makes that claim checkable."""
        for r in rows(
            conn,
            "SELECT slug, quote FROM coefficient "
            "WHERE confidence IN ('documented', 'code')",
        ):
            assert r["quote"], f"{r['slug']} is graded documented but quotes nothing"

    def test_quotes_are_not_paraphrases_of_the_slug(self, conn):
        for r in rows(conn, "SELECT slug, quote FROM coefficient WHERE quote IS NOT NULL"):
            assert len(r["quote"]) > 30, f"{r['slug']}'s quote is too short to check"

    def test_applies_to_names_something_specific(self, conn):
        """'all versions' is not a version range. Every value here should name a
        release, a range, or a hardware generation."""
        for r in rows(conn, "SELECT slug, applies_to FROM coefficient"):
            assert len(r["applies_to"]) > 6, f"{r['slug']}: applies_to is too vague"
            assert any(
                ch.isdigit() for ch in r["applies_to"]
            ), f"{r['slug']}: applies_to names no version — {r['applies_to']!r}"

    def test_estimates_explain_themselves(self, conn):
        """A figure resting on our own reasoning has to say what the reasoning
        was, or nobody can argue with it later."""
        for r in rows(
            conn, "SELECT slug, notes FROM coefficient WHERE confidence = 'estimate'"
        ):
            assert r["notes"], f"{r['slug']} is an estimate with no rationale"

    def test_bands_are_ordered(self, conn):
        bad = rows(
            conn,
            "SELECT slug FROM coefficient "
            "WHERE NOT (value_lo <= value_mode AND value_mode <= value_hi)",
        )
        assert not bad

    def test_wide_bands_are_not_graded_as_documented(self, conn):
        """A documented constant is a constant. If it needs a range, it is
        somebody's measurement, not somebody's specification."""
        for r in rows(
            conn,
            "SELECT slug, value_lo, value_hi FROM coefficient "
            "WHERE confidence = 'documented'",
        ):
            assert r["value_lo"] == r["value_hi"], (
                f"{r['slug']} is graded documented but carries a band"
            )


class TestModels:
    def test_every_term_explains_why_it_exists(self, conn):
        for r in rows(conn, "SELECT key, rationale FROM model_term"):
            assert (
                r["rationale"] and len(r["rationale"]) > 40
            ), f"term {r['key']} has no rationale"

    def test_every_model_has_a_floor(self, conn):
        floorless = rows(
            conn,
            "SELECT m.slug FROM model m WHERE NOT EXISTS ("
            "  SELECT 1 FROM model_term t WHERE t.model_id = m.id AND t.role='floor')",
        )
        assert not floorless

    def test_every_model_states_its_question_as_a_person_would_ask_it(self, conn):
        for r in rows(conn, "SELECT slug, question FROM model"):
            assert r["question"].endswith("?"), f"{r['slug']}: question is not a question"

    def test_constraint_terms_do_not_enter_the_arithmetic(self, conn):
        for r in rows(conn, "SELECT key, apply FROM model_term WHERE role='constraint'"):
            assert r["apply"] == "note", f"{r['key']} is a constraint that computes"

    def test_inputs_declared_are_inputs_used(self, conn):
        """An input the model never reads is a flag that silently does
        nothing."""
        unused = rows(
            conn,
            "SELECT i.key FROM model_input i WHERE NOT EXISTS ("
            "  SELECT 1 FROM model_term t "
            "  WHERE t.model_id = i.model_id AND t.input_key = i.key)",
        )
        assert not [r["key"] for r in unused]


class TestStubs:
    @pytest.mark.parametrize("slug", ["ebs", "clickhouse", "redis", "celery", "nvme-ssd"])
    def test_deferred_systems_exist_and_are_honestly_empty(self, conn, slug):
        """Named so the roadmap is visible, empty so nothing reads as
        researched when it is not."""
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM coefficient c "
            "JOIN system s ON s.id = c.system_id WHERE s.slug = ?",
            (slug,),
        ).fetchone()
        assert row["n"] == 0
