"""The OSS docs do not carry Preloop Enterprise-only governance review pages.

The weekly governance review ships only in the Enterprise plugin. Its guide
lives in the Enterprise repository, which adds it to the docs build.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_oss_docs_nav_has_no_weekly_governance_review() -> None:
    nav = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "governance-review" not in nav
    assert "Weekly governance review" not in nav


def test_oss_docs_have_no_weekly_governance_review_page() -> None:
    assert not (REPO_ROOT / "docs" / "guide" / "governance-review.md").exists()
    for page in (REPO_ROOT / "docs").rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        assert "](../guide/governance-review.md)" not in text, page
        assert "](guide/governance-review.md)" not in text, page
