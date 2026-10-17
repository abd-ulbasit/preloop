"""Target-aware notice copy without delivery or database access."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from preloop.services import policy_notice_delivery as delivery
from preloop.services.policy_notices import PolicyNoticeDigestSection


@pytest.mark.parametrize(
    ("target", "label"),
    [
        ("tool.args", "tool input"),
        ("tool.result", "tool result"),
        ("model.request", "model request"),
        ("model.response", "model response"),
    ],
)
def test_notice_and_digest_name_the_matched_target(target: str, label: str) -> None:
    """Email and digest text/HTML preserve the kind of matched activity."""
    hit = SimpleNamespace(
        target=target,
        rule_id="notify-example",
        rule_description="Example rule",
        excerpt="Example excerpt",
    )
    with patch.object(delivery, "_who", return_value="Jane Doe"):
        message = delivery.build_message(MagicMock(), hit)
    assert f"matched a {label} from Jane Doe" in message["text"]
    assert f"a {label} from Jane Doe" in message["html"]

    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    section = PolicyNoticeDigestSection(
        title="Policy notices",
        window_start=now - timedelta(days=7),
        window_end=now,
        rows=[
            SimpleNamespace(
                target=target,
                rule_id=hit.rule_id,
                count=1,
                last_username="Jane Doe",
                last_excerpt=hit.excerpt,
            )
        ],
    )
    assert label in section.render_text()
    assert label in section.render_html()
