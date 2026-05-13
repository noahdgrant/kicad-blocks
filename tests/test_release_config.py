"""Validate that the release-please + PyPI publish wiring is in place.

These are file-shape tests: the CI pipelines themselves run on GitHub, but we
can verify locally that the config files exist, parse, and reference the right
artifacts (correct package name, current version, OIDC publish action, etc.).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_release_please_config_targets_python_package() -> None:
    """release-please-config.json must configure the kicad-blocks package as a Python release."""
    config = json.loads(_read_text(REPO_ROOT / "release-please-config.json"))
    packages = config["packages"]
    root = packages["."]
    assert root["release-type"] == "python"
    assert root["package-name"] == "kicad-blocks"


def test_release_please_manifest_matches_pyproject_version() -> None:
    """The manifest's tracked version must equal the version in pyproject.toml."""
    manifest = json.loads(_read_text(REPO_ROOT / ".release-please-manifest.json"))
    pyproject = tomllib.loads(_read_text(REPO_ROOT / "pyproject.toml"))
    assert manifest["."] == pyproject["project"]["version"]


def test_release_please_workflow_runs_on_push_to_main() -> None:
    """The release-please workflow should trigger on push to main and invoke the action."""
    workflow = _read_text(REPO_ROOT / ".github" / "workflows" / "release-please.yml")
    assert "branches: [main]" in workflow or "branches:\n      - main" in workflow
    assert "googleapis/release-please-action" in workflow


def test_publish_pypi_workflow_runs_on_release_published() -> None:
    """The publish workflow should fire on the GitHub Release `published` event."""
    workflow = _read_text(REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml")
    assert "release:" in workflow
    assert "published" in workflow


def test_publish_pypi_workflow_uses_uv_build_and_trusted_publishing() -> None:
    """The publish workflow must build with uv and publish via OIDC trusted publishing."""
    workflow = _read_text(REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml")
    assert "astral-sh/setup-uv" in workflow
    assert "uv build" in workflow
    assert "pypa/gh-action-pypi-publish" in workflow
    # OIDC trusted publishing requires id-token: write at the job level.
    assert "id-token: write" in workflow


def test_publish_pypi_workflow_uploads_release_assets() -> None:
    """The publish workflow must upload the built sdist + wheel to the GitHub Release."""
    workflow = _read_text(REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml")
    # Uploading to the triggering release is done via `gh release upload`.
    assert "gh release upload" in workflow


def test_pyproject_has_complete_distribution_metadata() -> None:
    """pyproject.toml must declare the metadata PyPI needs for a real release."""
    pyproject = tomllib.loads(_read_text(REPO_ROOT / "pyproject.toml"))
    project = pyproject["project"]
    assert project["name"] == "kicad-blocks"
    assert project["description"]
    assert project["license"]
    assert project["requires-python"]
    assert project["classifiers"]
    urls = project["urls"]
    assert urls["Homepage"]
    assert urls["Repository"]
    assert urls["Issues"]


def test_readme_documents_pipx_install_and_reuse_sync_quickstart() -> None:
    """README should advertise pipx install plus a worked reuse+sync example."""
    readme = _read_text(REPO_ROOT / "README.md").lower()
    assert "pipx install kicad-blocks" in readme
    # The quick-start must walk through both reuse and sync (the v0.1 core loop).
    assert "kicad-blocks reuse" in readme
    assert "kicad-blocks sync" in readme
    # Issue tracker link for bug reports.
    assert "issues" in readme
