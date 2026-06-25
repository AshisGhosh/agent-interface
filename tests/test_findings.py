"""Tests for the per-project experiment findings ledger (agi finding/findings)."""

import sqlite3

import pytest

from agent_interface.findings import (
    compare_findings,
    list_findings,
    project_key,
    record_finding,
    remove_finding,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── record / list ────────────────────────────────────────────────────────────


def test_record_and_list(conn):
    record_finding(conn, project="/p", label="v2-minimap", metric="loss",
                   value=0.5, created_at=100.0)
    record_finding(conn, project="/p", label="v3-distance", metric="loss",
                   value=0.3, created_at=200.0)
    rows = list_findings(conn, "/p")
    assert [r["label"] for r in rows] == ["v3-distance", "v2-minimap"]  # newest first
    assert rows[0]["value"] == 0.3


def test_findings_are_project_scoped(conn):
    record_finding(conn, project="/a", label="alpha", created_at=1.0)
    record_finding(conn, project="/b", label="beta", created_at=1.0)
    assert [r["label"] for r in list_findings(conn, "/a")] == ["alpha"]
    assert [r["label"] for r in list_findings(conn, "/b")] == ["beta"]


def test_metric_and_label_filters(conn):
    record_finding(conn, project="/p", label="v1", metric="loss", value=1.0,
                   created_at=1.0)
    record_finding(conn, project="/p", label="v1", metric="acc", value=0.8,
                   created_at=2.0)
    record_finding(conn, project="/p", label="v2", metric="loss", value=0.5,
                   created_at=3.0)
    by_metric = list_findings(conn, "/p", metric="loss")
    assert {r["label"] for r in by_metric} == {"v1", "v2"}
    assert all(r["metric"] == "loss" for r in by_metric)
    by_label = list_findings(conn, "/p", label="v1")
    assert {r["metric"] for r in by_label} == {"loss", "acc"}


def test_note_persisted(conn):
    record_finding(conn, project="/p", label="v3", note="replaced minimap recon",
                   created_at=1.0)
    assert list_findings(conn, "/p")[0]["note"] == "replaced minimap recon"


def test_limit(conn):
    for i in range(5):
        record_finding(conn, project="/p", label=f"v{i}", created_at=float(i))
    assert len(list_findings(conn, "/p", limit=3)) == 3


# ── compare ──────────────────────────────────────────────────────────────────


def test_compare_higher_is_better(conn):
    record_finding(conn, project="/p", label="v2", metric="acc", value=0.70,
                   created_at=1.0)
    record_finding(conn, project="/p", label="v3", metric="acc", value=0.85,
                   created_at=2.0)
    ranked = compare_findings(conn, "/p", "acc")
    assert [e["label"] for e in ranked] == ["v3", "v2"]
    assert ranked[0]["best"] == 0.85


def test_compare_lower_is_better(conn):
    record_finding(conn, project="/p", label="v2", metric="loss", value=0.5,
                   created_at=1.0)
    record_finding(conn, project="/p", label="v3", metric="loss", value=0.3,
                   created_at=2.0)
    ranked = compare_findings(conn, "/p", "loss", higher_is_better=False)
    assert [e["label"] for e in ranked] == ["v3", "v2"]


def test_compare_picks_best_per_label(conn):
    # Same variant run twice; compare keeps the best value and counts runs.
    record_finding(conn, project="/p", label="v3", metric="acc", value=0.80,
                   created_at=1.0)
    record_finding(conn, project="/p", label="v3", metric="acc", value=0.90,
                   created_at=2.0)
    ranked = compare_findings(conn, "/p", "acc")
    assert len(ranked) == 1
    assert ranked[0]["best"] == 0.90
    assert ranked[0]["runs"] == 2


def test_compare_ignores_null_values_and_other_metrics(conn):
    record_finding(conn, project="/p", label="v1", metric="acc", value=None,
                   created_at=1.0)
    record_finding(conn, project="/p", label="v2", metric="loss", value=0.1,
                   created_at=2.0)
    record_finding(conn, project="/p", label="v3", metric="acc", value=0.5,
                   created_at=3.0)
    ranked = compare_findings(conn, "/p", "acc")
    assert [e["label"] for e in ranked] == ["v3"]


def test_compare_ties_are_deterministic(conn):
    record_finding(conn, project="/p", label="b", metric="acc", value=0.5,
                   created_at=1.0)
    record_finding(conn, project="/p", label="a", metric="acc", value=0.5,
                   created_at=2.0)
    ranked = compare_findings(conn, "/p", "acc")
    assert [e["label"] for e in ranked] == ["a", "b"]  # alphabetical tie-break


# ── remove ───────────────────────────────────────────────────────────────────


def test_remove(conn):
    fid = record_finding(conn, project="/p", label="temp", created_at=1.0)
    assert remove_finding(conn, "/p", fid) is True
    assert list_findings(conn, "/p") == []
    assert remove_finding(conn, "/p", fid) is False  # gone now


def test_remove_is_project_scoped(conn):
    fid = record_finding(conn, project="/a", label="keep", created_at=1.0)
    assert remove_finding(conn, "/b", fid) is False
    assert [r["label"] for r in list_findings(conn, "/a")] == ["keep"]


# ── project key ──────────────────────────────────────────────────────────────


def test_project_key_falls_back_to_cwd(tmp_path):
    assert project_key(str(tmp_path)) == str(tmp_path.resolve())
