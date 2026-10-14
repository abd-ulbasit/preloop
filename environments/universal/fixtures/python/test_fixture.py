"""Dependency-free fixture for mypy, ruff, and pytest."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right


def test_add() -> None:
    """The fixture assertion pytest collects."""
    assert add(2, 3) == 5
