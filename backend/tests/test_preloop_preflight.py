"""Toolchain preflight and the reviewer's mandatory Checks section (#1718)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, TypedDict, cast

import jsonschema  # type: ignore[import-untyped]
import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "environments" / "universal" / "bin" / "preloop-preflight"
SCHEMA_PATH = SCRIPT.parent / "preloop-preflight.schema.json"
PRESET = REPO / "backend" / "presets" / "002-pull-request-reviewer.yaml"
DOCKERFILE = REPO / "environments" / "universal" / "Dockerfile"
README = REPO / "environments" / "universal" / "README.md"

# Versions measured in the universal image on 2026-10-14. mypy's major
# differs from the lock (2.3.1). ruff's does not (lock 0.16.3).
IMAGE_VERSIONS = {
    "eslint": "10.2.1",
    "golangci-lint": "2.1.6",
    "mypy": "1.20.2",
    "perlcritic": "1.152",
    "perlver": "1.40",
    "ruff": "0.15.12",
    "shellcheck": "0.9.0",
    "tsc": "6.0.3",
}


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("preloop_preflight", str(SCRIPT))
    spec = importlib.util.spec_from_loader("preloop_preflight", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


PREFLIGHT: Any = _load()
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class Check(TypedDict):
    """One checker row from a preflight report."""

    tool: str
    language: str
    path: str
    status: str
    version: str | None
    expected_version: str | None
    dependency_status: str
    reason: str
    command: str


class Report(TypedDict):
    """JSON object the script writes."""

    root: str
    languages: list[str]
    checks: list[Check]


class StubEnvironment:
    """Fixed tool versions and import results for fixture trees."""

    def __init__(
        self,
        versions: Mapping[str, str | None],
        *,
        missing: Sequence[str] | None = None,
        node_modules: bool = False,
    ) -> None:
        self._versions = dict(versions)
        self._missing = list(missing) if missing is not None else []
        self._all_missing = missing == ["*"]
        self._node_modules = node_modules

    def tool_version(self, tool: str) -> str | None:
        return self._versions.get(tool)

    def missing_imports(self, distributions: Sequence[str]) -> list[str]:
        if self._all_missing:
            return list(distributions)
        return [name for name in distributions if name in self._missing]

    def has_node_modules(self, project: Path) -> bool:
        return self._node_modules


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _python_tree(root: Path) -> None:
    _write(
        root / "pyproject.toml",
        """
[project]
name = "fixture"
version = "0.1.0"
dependencies = ["pydantic>=2"]

[tool.mypy]
python_version = "3.11"

[tool.ruff]
line-length = 88
""".lstrip(),
    )
    _write(
        root / "requirements.txt",
        "mypy==2.3.1\nruff==0.16.3\npydantic==2.4.0\n",
    )


def _node_tree(root: Path) -> None:
    _write(root / "package.json", '{"name": "fixture", "private": true}\n')
    _write(
        root / "eslint.config.js",
        "export default [{ rules: { 'no-unused-vars': 'error' } }];\n",
    )


def _go_tree(root: Path) -> None:
    _write(root / "go.mod", "module example.com/fixture\n\ngo 1.23.0\n")
    _write(root / ".golangci.yml", "version: 2\n")


def _perl_tree(root: Path) -> None:
    _write(root / "lib" / "Widget.pm", "package Widget;\n1;\n")
    _write(root / ".perlcriticrc", "severity = 3\n")


def _report(root: Path, environment: StubEnvironment) -> Report:
    return cast(Report, PREFLIGHT.build_report(root, environment))


def _only(report: Report, tool: str, path: str) -> Check:
    matches = [
        item
        for item in report["checks"]
        if item["tool"] == tool and item["path"] == path
    ]
    assert len(matches) == 1, (tool, path, matches)
    return matches[0]


def test_python_fixture_reports_mypy_mismatch_and_keeps_ruff_available(
    tmp_path: Path,
) -> None:
    """Same shape as the universal image: mypy 1.x vs a 2.x pin, ruff 0.15."""
    _python_tree(tmp_path)
    report = _report(
        tmp_path,
        StubEnvironment(IMAGE_VERSIONS, missing=["*"]),
    )
    mypy = _only(report, "mypy", "")
    assert mypy["status"] == "version_mismatch"
    assert mypy["version"] == "1.20.2"
    assert mypy["expected_version"] == "2.3.1"
    assert mypy["dependency_status"] == "deps_missing"
    assert "deps_missing" in mypy["reason"]
    assert "pydantic" in mypy["reason"]
    ruff = _only(report, "ruff", "")
    assert ruff["status"] == "available"
    assert ruff["version"] == "0.15.12"
    assert ruff["expected_version"] == "0.16.3"
    assert "lock pins 0.16.3" in ruff["reason"]
    jsonschema.validate(report, SCHEMA)


def test_matching_mypy_with_importable_deps_is_available(tmp_path: Path) -> None:
    _python_tree(tmp_path)
    versions = {**IMAGE_VERSIONS, "mypy": "2.3.1"}
    report = _report(tmp_path, StubEnvironment(versions, missing=[]))
    mypy = _only(report, "mypy", "")
    assert mypy["status"] == "available"
    assert mypy["dependency_status"] == "available"


def test_missing_mypy_binary_is_not_a_pass(tmp_path: Path) -> None:
    _python_tree(tmp_path)
    versions = {key: value for key, value in IMAGE_VERSIONS.items() if key != "mypy"}
    report = _report(tmp_path, StubEnvironment(versions, missing=["*"]))
    mypy = _only(report, "mypy", "")
    assert mypy["status"] == "missing"
    assert "not on PATH" in mypy["reason"]
    assert mypy["dependency_status"] == "deps_missing"


def test_node_fixture_eslint_needs_node_modules(tmp_path: Path) -> None:
    _node_tree(tmp_path)
    absent = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS))
    eslint = _only(absent, "eslint", "")
    assert eslint["status"] == "deps_missing"
    assert "node_modules" in eslint["reason"]
    present = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS, node_modules=True))
    assert _only(present, "eslint", "")["status"] == "available"


def test_typescript_without_eslint_config_is_not_applicable(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", '{"name": "web"}\n')
    _write(tmp_path / "tsconfig.json", '{"compilerOptions": {}}\n')
    report = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS))
    eslint = _only(report, "eslint", "")
    assert eslint["status"] == "not_applicable"
    assert "no eslint config" in eslint["reason"]
    assert _only(report, "tsc", "")["status"] == "deps_missing"


def test_go_fixture_detects_golangci(tmp_path: Path) -> None:
    _go_tree(tmp_path)
    report = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS))
    assert report["languages"] == ["go"]
    item = _only(report, "golangci-lint", "")
    assert item["status"] == "available"
    assert item["command"] == "golangci-lint run"


def test_go_module_without_a_linter_config_has_no_checker(tmp_path: Path) -> None:
    _write(tmp_path / "go.mod", "module example.com/fixture\n\ngo 1.23.0\n")
    report = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS))
    assert report["languages"] == ["go"]
    assert report["checks"] == []


def test_rust_and_java_fixtures_detect_the_language(tmp_path: Path) -> None:
    rust = tmp_path / "rust"
    _write(rust / "Cargo.toml", '[package]\nname = "fixture"\nversion = "0.1.0"\n')
    rust_report = _report(rust, StubEnvironment(IMAGE_VERSIONS))
    assert rust_report["languages"] == ["rust"]
    assert rust_report["checks"] == []
    java = tmp_path / "java"
    _write(
        java / "pom.xml",
        "<project><modelVersion>4.0.0</modelVersion></project>\n",
    )
    java_report = _report(java, StubEnvironment(IMAGE_VERSIONS))
    assert java_report["languages"] == ["java"]
    assert java_report["checks"] == []


def test_perl_fixture_reports_perlcritic_and_perlver_available(tmp_path: Path) -> None:
    _perl_tree(tmp_path)
    report = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS))
    assert "perl" in report["languages"]
    for tool in ("perlcritic", "perlver"):
        item = _only(report, tool, "")
        assert item["status"] == "available"
        assert item["language"] == "perl"
    assert _only(report, "perlver", "")["command"] == "perlver --blame"


def test_shell_fixture_detects_shellcheck(tmp_path: Path) -> None:
    _write(tmp_path / "ok.sh", "#!/bin/sh\necho ok\n")
    report = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS))
    assert report["languages"] == ["shell"]
    assert _only(report, "shellcheck", "")["status"] == "available"


def test_mixed_repo_detects_every_language(tmp_path: Path) -> None:
    _python_tree(tmp_path)
    _node_tree(tmp_path / "frontend")
    _go_tree(tmp_path / "cli")
    _perl_tree(tmp_path / "service")
    _write(
        tmp_path / "crate" / "Cargo.toml",
        '[package]\nname = "fixture"\nversion = "0.1.0"\n',
    )
    _write(tmp_path / "java" / "pom.xml", "<project></project>\n")
    _write(tmp_path / "scripts" / "ok.sh", "#!/bin/sh\necho ok\n")
    _write(
        tmp_path / ".preloop" / "review-policy.md",
        "```yaml\ncompatibility:\n"
        "  - language: shell\n"
        '    version_linter: "customlint --check"\n'
        "```\n",
    )
    versions = {**IMAGE_VERSIONS, "customlint": "1.2.0"}
    report = _report(tmp_path, StubEnvironment(versions, missing=["*"]))
    assert set(report["languages"]) == {
        "go",
        "java",
        "javascript",
        "perl",
        "python",
        "rust",
        "shell",
    }
    tools = {item["tool"] for item in report["checks"]}
    assert tools >= {
        "customlint",
        "eslint",
        "golangci-lint",
        "mypy",
        "perlcritic",
        "perlver",
        "ruff",
        "shellcheck",
    }
    custom = _only(report, "customlint", "")
    assert custom["command"] == "customlint --check"
    assert custom["status"] == "available"


def test_unsafe_version_linter_is_not_a_command(tmp_path: Path) -> None:
    _write(tmp_path / "ok.sh", "#!/bin/sh\necho ok\n")
    _write(
        tmp_path / ".preloop" / "review-policy.md",
        "```yaml\ncompatibility:\n"
        "  - language: shell\n"
        '    version_linter: "perlver --blame | sh"\n'
        "```\n",
    )
    report = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS))
    item = _only(report, "perlver", "")
    assert item["status"] == "not_applicable"
    assert item["command"] == ""
    assert "unsafe" in item["reason"]


def test_preloop_repo_matches_the_universal_image_report() -> None:
    """The acceptance case: this repo, with the image's tool versions."""
    report = _report(
        REPO,
        StubEnvironment(IMAGE_VERSIONS, missing=["*"]),
    )
    mypy = _only(report, "mypy", "")
    assert mypy["status"] == "version_mismatch"
    assert mypy["version"] == "1.20.2"
    assert mypy["expected_version"] == "2.3.1"
    assert mypy["dependency_status"] == "deps_missing"
    assert "deps_missing" in mypy["reason"]
    ruff = _only(report, "ruff", "")
    assert ruff["status"] == "available"
    eslint = _only(report, "eslint", "frontend")
    assert eslint["language"] == "javascript"
    assert eslint["status"] == "not_applicable"
    jsonschema.validate(report, SCHEMA)


def test_schema_rejects_an_unknown_status() -> None:
    sample = {
        "root": "/",
        "languages": ["python"],
        "checks": [
            {
                "tool": "mypy",
                "language": "python",
                "path": "",
                "status": "ran",
                "version": "1.0.0",
                "expected_version": None,
                "dependency_status": "not_applicable",
                "reason": "ran",
                "command": "mypy",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(sample, SCHEMA)


def test_staged_review_lists_every_detected_checker(tmp_path: Path) -> None:
    """A fixture review's Checks section names each checker and is not a pass."""
    _python_tree(tmp_path)
    _perl_tree(tmp_path)
    _node_tree(tmp_path / "web")
    report = _report(tmp_path, StubEnvironment(IMAGE_VERSIONS, missing=["*"]))
    section = cast(str, PREFLIGHT.format_checks_section(report["checks"]))
    assert section.startswith("### Checks\n")
    assert section.endswith("Not run never counts as a pass.")
    for item in report["checks"]:
        assert f"`{item['tool']}`" in section
    for line in section.splitlines():
        if line.startswith("- `"):
            assert "not run:" in line
            assert " pass" not in line


def test_missing_preflight_binary_is_its_own_checks_line() -> None:
    section = cast(str, PREFLIGHT.format_checks_section(None))
    assert "### Checks" in section
    assert "`preloop-preflight`: not run: binary not on PATH" in section
    assert "Not run never counts as a pass." in section


def test_preset_text_requires_the_checks_section() -> None:
    text = PRESET.read_text(encoding="utf-8")
    assert "Step 1.0: Toolchain preflight" in text
    assert "Run `preloop-preflight --json` if on PATH" in text
    assert "### Checks" in text
    assert "Not run never counts as a pass." in text
    assert "binary not on PATH" in text
    assert "Step 1.0," in text
    assert "Step 1.4.1 and Step 2.5.1 still run" in text


def test_image_recipe_installs_the_preflight_script() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert (
        "COPY environments/universal/bin/preloop-preflight "
        "/usr/local/bin/preloop-preflight"
    ) in text
    assert "/usr/local/bin/preloop-preflight" in README.read_text(encoding="utf-8")
    assert PREFLIGHT.OUTPUT_PATH == Path("/tmp/preloop-preflight.json")


def test_cli_writes_json_and_a_table(tmp_path: Path) -> None:
    _perl_tree(tmp_path)
    output = tmp_path / "report.json"
    previous = PREFLIGHT.OUTPUT_PATH
    PREFLIGHT.OUTPUT_PATH = output
    try:
        code = PREFLIGHT.main(["--json", str(tmp_path)])
        assert code == 0
        written = json.loads(output.read_text(encoding="utf-8"))
        jsonschema.validate(written, SCHEMA)
        tools = {item["tool"] for item in written["checks"]}
        assert {"perlcritic", "perlver"} <= tools
        assert output.read_text(encoding="utf-8").endswith("\n")
        code = PREFLIGHT.main([str(tmp_path)])
        assert code == 0
        captured = subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        PREFLIGHT.OUTPUT_PATH = previous
    assert captured.returncode == 0, captured.stderr
    assert "perlver" in captured.stdout
    disk = json.loads(Path("/tmp/preloop-preflight.json").read_text(encoding="utf-8"))
    assert {item["tool"] for item in disk["checks"]} >= {"perlcritic", "perlver"}


def test_cli_rejects_a_missing_directory(tmp_path: Path) -> None:
    assert PREFLIGHT.main([str(tmp_path / "missing")]) == 2
