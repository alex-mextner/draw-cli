"""Executable documentation checks for facts users depend on."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
DETAILS = (ROOT / "docs" / "chatgpt-subscription.md").read_text(encoding="utf-8")


def test_readme_contains_how_it_works_mermaid_diagram():
    assert "## How it works" in README
    assert "```mermaid" in README
    assert "codex exec" in README
    assert "native image" in README.lower()
    assert "atomic" in README.lower()


def test_readme_distinguishes_plan_usage_from_api_billing():
    assert "ChatGPT plan" in README
    assert "API key" in README
    assert "no api fallback" in README.lower()


def test_readme_does_not_claim_hf_token_means_free_inference():
    stale_claim = "a free-tier read token is enough for inference"
    assert stale_claim not in README
    assert "credits" in README.lower()


def test_readme_documents_limits_that_change_behavior():
    assert "1 MiB" in README
    assert "five" in README.lower() and "reference" in README.lower()
    assert "32 MiB" in README


def test_detailed_doc_matches_thread_scoped_artifact_contract():
    assert "wall-clock" in DETAILS.lower() or "mtime" in DETAILS.lower()
    assert "thread" in DETAILS.lower() and "UUID" in DETAILS
    assert "stale" not in DETAILS.lower() or "other thread" in DETAILS.lower()
