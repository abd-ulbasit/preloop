"""The OSS docs do not carry Preloop Enterprise-only governance review pages.

The weekly governance review ships only in the Enterprise plugin. Its guide
lives in the Enterprise repository, which adds it to the docs build.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_oss_docs_nav_has_no_weekly_governance_review() -> None:
    config = yaml.load(
        (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    nav = str(config["nav"])
    assert "governance-review" not in nav
    assert "Weekly governance review" not in nav


def test_oss_docs_have_no_weekly_governance_review_page() -> None:
    assert not (REPO_ROOT / "docs" / "guide" / "governance-review.md").exists()
    pages = list((REPO_ROOT / "docs").rglob("*.md"))
    pages.extend(page for page in REPO_ROOT.glob("*.md") if page.name != "CHANGELOG.md")
    pages.extend((REPO_ROOT / "helm").rglob("*.md"))
    pages.extend((REPO_ROOT / "helm").rglob("*.yaml"))
    pages.append(REPO_ROOT / ".env.example")
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "governance-review.md" not in text, page
