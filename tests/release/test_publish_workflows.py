from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
TESTPYPI_PATH = ROOT / ".github" / "workflows" / "publish-testpypi.yml"
PYPI_PATH = ROOT / ".github" / "workflows" / "publish-pypi.yml"
PUBLISH_ACTION = (
    "pypa/gh-action-pypi-publish@"
    "dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
)


def _workflow(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _uses(job: dict[str, Any]) -> list[str]:
    return [step["uses"] for step in job["steps"] if "uses" in step]


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_workflow_yaml_and_target_specific_triggers() -> None:
    testpypi = _workflow(TESTPYPI_PATH)
    pypi = _workflow(PYPI_PATH)

    assert testpypi["on"] == {"workflow_dispatch": None}
    assert pypi["on"] == {"release": {"types": ["published"]}}
    assert testpypi["permissions"] == {"contents": "read"}
    assert pypi["permissions"] == {"contents": "read"}
    assert testpypi["concurrency"]["cancel-in-progress"] is False
    assert pypi["concurrency"]["cancel-in-progress"] is False
    assert "tag_name" in pypi["concurrency"]["group"]


def test_only_publish_jobs_receive_oidc_and_exact_environments() -> None:
    expected_environments = {
        TESTPYPI_PATH: (
            "testpypi",
            "https://test.pypi.org/project/wyrd-cli/",
        ),
        PYPI_PATH: ("pypi", "https://pypi.org/project/wyrd-cli/"),
    }

    for path, (name, url) in expected_environments.items():
        workflow = _workflow(path)
        oidc_jobs = {
            job_name
            for job_name, job in workflow["jobs"].items()
            if job.get("permissions", {}).get("id-token") == "write"
        }
        assert oidc_jobs == {"publish"}
        assert workflow["jobs"]["publish"]["permissions"] == {
            "id-token": "write"
        }
        assert workflow["jobs"]["publish"]["environment"] == {
            "name": name,
            "url": url,
        }


def test_actions_are_immutable_pins_and_publishers_use_downloads() -> None:
    pin = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")

    for path in (TESTPYPI_PATH, PYPI_PATH):
        workflow = _workflow(path)
        all_uses = [
            use
            for job in workflow["jobs"].values()
            for use in _uses(job)
        ]
        assert all_uses
        assert all(pin.fullmatch(use) for use in all_uses)

        publisher = workflow["jobs"]["publish"]
        publisher_uses = _uses(publisher)
        assert publisher_uses[-1] == PUBLISH_ACTION
        assert publisher_uses[0].startswith("actions/download-artifact@")
        assert not any(use.startswith("actions/checkout@") for use in publisher_uses)
        assert not any(use.startswith("astral-sh/setup-uv@") for use in publisher_uses)
        publisher_runs = _runs(publisher)
        assert "scripts/release.py" not in publisher_runs
        assert "uv build" not in publisher_runs
        assert "uv run" not in publisher_runs
        assert "sha256sum --check" in publisher_runs

        build = workflow["jobs"]["build"]
        build_uses = _uses(build)
        assert any(use.startswith("actions/upload-artifact@") for use in build_uses)
        assert PUBLISH_ACTION not in build_uses
        assert "scripts/release.py" in _runs(build)


def test_release_ref_version_and_matrix_invariants() -> None:
    testpypi = _workflow(TESTPYPI_PATH)
    pypi = _workflow(PYPI_PATH)
    expected_matrix = {"python-version": ["3.12", "3.13", "3.14"]}

    assert testpypi["jobs"]["verify"]["strategy"]["matrix"] == expected_matrix
    assert pypi["jobs"]["verify"]["strategy"]["matrix"] == expected_matrix

    for job_name in ("verify", "build"):
        test_checkout = next(
            step
            for step in testpypi["jobs"][job_name]["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        assert test_checkout["with"]["ref"] == "${{ github.sha }}"

        production_checkout = next(
            step
            for step in pypi["jobs"][job_name]["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        assert production_checkout["with"]["ref"] == (
            "${{ github.event.release.tag_name }}"
        )

    test_verify = _runs(testpypi["jobs"]["verify"])
    production_verify = _runs(pypi["jobs"]["verify"])
    assert "merge-base --is-ancestor" in test_verify
    assert "refs/remotes/origin/main" in test_verify
    assert "without a production tag" in test_verify
    assert "^v(0|[1-9][0-9]*)" in production_verify
    assert "validate_repository(tag=os.environ[\"GITHUB_REF\"])" in production_verify

    test_build = _runs(testpypi["jobs"]["build"])
    production_build = _runs(pypi["jobs"]["build"])
    canonical = (
        "uv run --locked --group release python scripts/release.py "
        "--artifact-dir dist/release --clear"
    )
    assert canonical in test_build
    assert "--tag" not in test_build
    assert f'{canonical} --tag "$GITHUB_REF"' in production_build


def test_upload_targets_credentials_and_handoff_are_narrow() -> None:
    test_text = TESTPYPI_PATH.read_text(encoding="utf-8")
    production_text = PYPI_PATH.read_text(encoding="utf-8")
    combined = (test_text + production_text).lower()

    for forbidden in (
        "pull_request_target",
        "secrets.",
        "api_token",
        "api-token",
        "pypi_token",
        "password:",
        "skip-existing",
    ):
        assert forbidden not in combined

    assert test_text.count("repository-url:") == 1
    assert "repository-url: https://test.pypi.org/legacy/" in test_text
    assert "repository-url:" not in production_text

    for path in (TESTPYPI_PATH, PYPI_PATH):
        workflow = _workflow(path)
        publish_steps = workflow["jobs"]["publish"]["steps"]
        publish = next(step for step in publish_steps if step.get("uses") == PUBLISH_ACTION)
        assert publish["with"]["packages-dir"] == "release/packages/"
        assert publish["with"]["attestations"] is True
        assert publish["with"]["print-hash"] is True

        download = next(
            step
            for step in publish_steps
            if step.get("uses", "").startswith("actions/download-artifact@")
        )
        assert download["with"] == {
            "artifact-ids": "${{ needs.build.outputs.artifact-id }}",
            "path": "release/",
        }

        upload = next(
            step
            for step in workflow["jobs"]["build"]["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        assert upload["with"]["path"] == "dist/handoff/"
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["retention-days"] == 30
        assert "test \"$(find dist/handoff -type f | wc -l)\" -eq 3" in _runs(
            workflow["jobs"]["build"]
        )


def test_operator_runbook_has_exact_setup_and_safety_contract() -> None:
    runbook = (ROOT / "RELEASING.md").read_text(encoding="utf-8")

    for expected in (
        "Linux only",
        "Python 3.12, 3.13, and 3.14",
        "uv run --locked --group release python scripts/release.py --artifact-dir dist/release --clear",
        ".github/workflows/publish-testpypi.yml",
        ".github/workflows/publish-pypi.yml",
        "`testpypi`",
        "`pypi`",
        "`debonzi`",
        "`wyrd`",
        "`publish-testpypi.yml`",
        "`publish-pypi.yml`",
        "recovery codes",
        "--no-deps",
        "cannot be overwritten",
        "Do not blindly rerun",
        "yank",
        "`0.1.1`",
        "Never reuse old local `dist/` artifacts",
    ):
        assert expected in runbook

    assert "does **not** mean they have already been configured" in runbook
