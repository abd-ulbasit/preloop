"""Write /opt/preloop-environment.json for the universal Codex image.

Run at image build time, after the Codex entrypoint has selected the
Preloop runtime versions. The file keeps the environment-profile protocol
fields (version 1, harness codex) and records each required tool's version.
It refuses a base image that is not pinned by digest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

DIGEST_REF = re.compile(r"^[^\s@]+@sha256:[a-f0-9]{64}$")

# Tool name, then the argv that prints a version. perlver 1.40 rejects
# --version, so its version is the Perl::MinimumVersion module version.
TOOL_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mypy", ("mypy", "--version")),
    ("ruff", ("ruff", "--version")),
    ("pytest", ("pytest", "--version")),
    ("javac", ("javac", "-version")),
    ("mvn", ("mvn", "-version")),
    ("go", ("go", "version")),
    ("golangci-lint", ("golangci-lint", "version")),
    ("cargo", ("cargo", "--version")),
    ("rustc", ("rustc", "--version")),
    ("cargo-clippy", ("cargo-clippy", "--version")),
    ("tsc", ("tsc", "--version")),
    ("eslint", ("eslint", "--version")),
    ("perl", ("perl", "-e", r'printf "%vd\n", $^V')),
    ("cpanm", ("cpanm", "--version")),
    (
        "perlver",
        (
            "perl",
            "-MPerl::MinimumVersion",
            "-e",
            'print $Perl::MinimumVersion::VERSION, "\n"',
        ),
    ),
    ("perlcritic", ("perlcritic", "--version")),
    ("prove", ("prove", "--version")),
    ("shellcheck", ("shellcheck", "--version")),
)

CommandRunner = Callable[[Sequence[str]], str]


def first_version_line(tool: str, text: str) -> str:
    """Return the first non-empty line that contains a digit.

    Args:
        tool: Tool name, used in the error when no version line exists.
        text: Combined stdout and stderr from the version command.

    Returns:
        The version line, stripped.

    Raises:
        ValueError: The output has no line that contains a version digit.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(character.isdigit() for character in stripped):
            return stripped
    raise ValueError(f"empty version for {tool}")


def probe_versions(run: CommandRunner) -> dict[str, str]:
    """Run each tool's version command.

    Args:
        run: Callable that executes an argv and returns its output.

    Returns:
        Tool name to version line.

    Raises:
        ValueError: A tool is missing or prints no version.
    """
    versions: dict[str, str] = {}
    for tool, argv in TOOL_COMMANDS:
        try:
            text = run(argv)
        except OSError as exc:
            raise ValueError(f"missing required tool: {tool}") from exc
        versions[tool] = first_version_line(tool, text)
    return versions


def build_manifest(*, base_image: str, tools: Mapping[str, str]) -> dict[str, object]:
    """Return the environment manifest.

    Args:
        base_image: Base image reference, which must be ``name@sha256:...``.
        tools: Tool name to a non-empty version line.

    Returns:
        Manifest mapping with protocol fields, the base image, and tools.

    Raises:
        ValueError: The base is not digest-pinned or a tool version is missing.
    """
    if not DIGEST_REF.match(base_image):
        raise ValueError(
            f"base image must be pinned by digest (name@sha256:...): {base_image!r}"
        )
    expected = [name for name, _argv in TOOL_COMMANDS]
    missing = [name for name in expected if not tools.get(name, "").strip()]
    if missing:
        raise ValueError("missing required tool: " + ", ".join(missing))
    return {
        "version": 1,
        "harness": "codex",
        "base_image": base_image,
        "tools": {name: tools[name].strip() for name in expected},
    }


def subprocess_runner(argv: Sequence[str]) -> str:
    """Run a version command and return its combined output.

    Args:
        argv: Command and arguments.

    Returns:
        Combined stdout and stderr.

    Raises:
        ValueError: The command exits non-zero.
        OSError: The command cannot be started.
    """
    env = os.environ.copy()
    cpanm_home: str | None = None
    if argv and argv[0] == "cpanm":
        cpanm_home = tempfile.mkdtemp(prefix="preloop-cpanm-")
        env["PERL_CPANM_HOME"] = cpanm_home
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    finally:
        if cpanm_home is not None:
            shutil.rmtree(cpanm_home, ignore_errors=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(f"missing required tool: {argv[0]}: {detail}")
    return result.stdout + result.stderr


def main(argv: list[str] | None = None) -> int:
    """Probe the toolchain and write the manifest.

    Args:
        argv: CLI arguments, or ``None`` to read ``sys.argv``.

    Returns:
        Process exit code. ``0`` on success, ``1`` when the manifest is refused.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(
            base_image=args.base_image,
            tools=probe_versions(subprocess_runner),
        )
    except (OSError, ValueError) as exc:
        print(f"preloop-environment: {exc}", file=sys.stderr)
        return 1
    try:
        args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"preloop-environment: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
