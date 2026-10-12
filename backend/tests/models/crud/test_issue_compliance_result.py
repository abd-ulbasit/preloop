"""Tests for IssueComplianceResult CRUD operations."""

from uuid import uuid4

from sqlalchemy.orm import Session

from preloop.models.crud.issue_compliance_result import issue_compliance_result
from preloop.models.models.issue_compliance_result import IssueComplianceResult


class TestIssueComplianceResultCRUD:
    """Test CRUD operations for IssueComplianceResult."""

    def test_get_by_issue_id_and_prompt_id(
        self, db_session: Session, create_issue, create_tracker
    ):
        """Test getting compliance result by issue and prompt ID."""
        tracker = create_tracker()
        issue = create_issue(tracker=tracker)

        # Create a compliance result
        result = IssueComplianceResult(
            issue_id=issue.id,
            prompt_id="dor_check",
            name="Definition of Ready",
            compliance_factor=0.95,
            reason="Meets all criteria",
            suggestion="Continue with current approach",
        )
        db_session.add(result)
        db_session.commit()

        # Get without account filter
        found = issue_compliance_result.get_by_issue_id_and_prompt_id(
            db_session, issue_id=issue.id, prompt_id="dor_check"
        )
        assert found is not None
        assert found.id == result.id
        assert found.compliance_factor == 0.95

        # Get with account filter
        found = issue_compliance_result.get_by_issue_id_and_prompt_id(
            db_session,
            issue_id=issue.id,
            prompt_id="dor_check",
            account_id=tracker.account_id,
        )
        assert found is not None
        assert found.id == result.id

        # Get with wrong account (use a valid UUID that doesn't exist)
        wrong_account_uuid = uuid4()
        found = issue_compliance_result.get_by_issue_id_and_prompt_id(
            db_session,
            issue_id=issue.id,
            prompt_id="dor_check",
            account_id=wrong_account_uuid,
        )
        assert found is None

        # Get with wrong prompt_id
        found = issue_compliance_result.get_by_issue_id_and_prompt_id(
            db_session, issue_id=issue.id, prompt_id="wrong_prompt"
        )
        assert found is None

    def test_delete_by_issue_id(
        self, db_session: Session, create_issue, create_tracker
    ):
        """Test deleting all compliance results for an issue."""
        tracker = create_tracker()
        issue = create_issue(tracker=tracker)

        # Create multiple compliance results for the issue
        result1 = IssueComplianceResult(
            issue_id=issue.id,
            prompt_id="dor_check",
            name="Definition of Ready",
            compliance_factor=0.95,
            reason="Meets all criteria",
            suggestion="Continue with current approach",
        )
        result2 = IssueComplianceResult(
            issue_id=issue.id,
            prompt_id="dod_check",
            name="Definition of Done",
            compliance_factor=0.45,
            reason="Missing acceptance criteria",
            suggestion="Add clear acceptance criteria",
        )
        db_session.add_all([result1, result2])
        db_session.commit()

        # Verify they exist
        results = (
            db_session.query(IssueComplianceResult)
            .filter(IssueComplianceResult.issue_id == issue.id)
            .all()
        )
        assert len(results) == 2

        # Delete all results for the issue
        num_deleted = issue_compliance_result.delete_by_issue_id(
            db_session, issue_id=issue.id
        )
        assert num_deleted == 2

        # Verify they're gone
        results = (
            db_session.query(IssueComplianceResult)
            .filter(IssueComplianceResult.issue_id == issue.id)
            .all()
        )
        assert len(results) == 0

    def test_create_compliance_result(
        self, db_session: Session, create_issue, create_tracker
    ):
        """Test creating a compliance result."""
        tracker = create_tracker()
        issue = create_issue(tracker=tracker)

        result_data = {
            "issue_id": issue.id,
            "prompt_id": "acceptance_criteria",
            "name": "Acceptance Criteria Check",
            "compliance_factor": 0.88,
            "reason": "All acceptance criteria are clearly defined",
            "suggestion": "Consider adding edge cases",
        }

        result = issue_compliance_result.create(db_session, obj_in=result_data)

        assert result.id is not None
        assert result.issue_id == issue.id
        assert result.prompt_id == "acceptance_criteria"
        assert result.name == "Acceptance Criteria Check"
        assert result.compliance_factor == 0.88
        assert result.reason == "All acceptance criteria are clearly defined"
        assert result.suggestion == "Consider adding edge cases"

    def test_update_compliance_result(
        self, db_session: Session, create_issue, create_tracker
    ):
        """Test updating a compliance result."""
        tracker = create_tracker()
        issue = create_issue(tracker=tracker)

        # Create initial result
        result = IssueComplianceResult(
            issue_id=issue.id,
            prompt_id="test_coverage",
            name="Test Coverage",
            compliance_factor=0.30,
            reason="Insufficient test coverage",
            suggestion="Add more unit tests",
        )
        db_session.add(result)
        db_session.commit()

        # Update the result
        updated = issue_compliance_result.update(
            db_session,
            db_obj=result,
            obj_in={"compliance_factor": 0.85, "reason": "Good test coverage"},
        )

        assert updated.compliance_factor == 0.85
        assert updated.reason == "Good test coverage"
        assert updated.prompt_id == "test_coverage"  # Unchanged


def test_get_for_issues_and_prompt_is_one_scoped_query(
    db_session: Session, create_issue, create_tracker
):
    """Batch lookup returns only stored rows of the caller's account."""
    tracker = create_tracker()
    scored = create_issue(tracker=tracker)
    unscored = create_issue(tracker=tracker)
    other_tracker = create_tracker()
    foreign = create_issue(tracker=other_tracker)
    for issue, prompt in ((scored, "dor"), (scored, "other"), (foreign, "dor")):
        db_session.add(
            IssueComplianceResult(
                issue_id=issue.id,
                prompt_id=prompt,
                name="DoR",
                compliance_factor=0.5,
                reason="r",
                suggestion="s",
            )
        )
    db_session.commit()

    rows = issue_compliance_result.get_for_issues_and_prompt(
        db_session,
        issue_ids=[str(scored.id), str(unscored.id), str(foreign.id)],
        prompt_id="dor",
        account_id=tracker.account_id,
    )

    assert [(r.issue_id, r.prompt_id) for r in rows] == [(scored.id, "dor")]
    assert (
        issue_compliance_result.get_for_issues_and_prompt(
            db_session, issue_ids=[], prompt_id="dor", account_id=tracker.account_id
        )
        == []
    )
