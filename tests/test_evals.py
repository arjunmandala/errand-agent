"""Runs every eval scenario under pytest and regenerates evals/results/eval_results.md + eval_table.csv."""
from pathlib import Path

import pytest

from evals.run import run_one, write
from evals.scenarios import SCENARIOS

ROWS: dict[str, dict] = {}
OUT = Path(__file__).resolve().parents[1] / "evals" / "results"


@pytest.fixture(scope="module", autouse=True)
def table():
    yield
    if len(ROWS) == len(SCENARIOS):  # only a full run rewrites the table
        write([ROWS[s.name] for s in SCENARIOS], OUT)


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario(sc, tmp_path):
    row = run_one(sc, tmp_path)
    ROWS[sc.name] = row
    assert row["result"] == "PASS", row["notes"]


def test_at_least_20_scenarios_cover_required_categories():
    cats = {s.category for s in SCENARIOS}
    required = {"adversarial input", "prompt injection", "price change after approval", "timeout after success",
                "slot conflict", "repeated confirmation", "memory correction", "memory forgetting",
                "restart mid-booking", "proactive cancellation", "context budget", "limit exhaustion", "tool failures"}
    assert len(SCENARIOS) >= 20 and required <= cats
