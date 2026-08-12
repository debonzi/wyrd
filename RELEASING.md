# Releasing Wyrd

This is the maintainer runbook for publishing `wyrd-cli`. The GitHub environments
and Trusted Publishers described below are one-time remote setup; their presence in
this document or in a workflow does **not** mean they have already been configured.
Set them up only after the publishing workflows have been reviewed and merged to
`main`.

## Release identity and support

| Item | Value |
| --- | --- |
| Distribution / index project | `wyrd-cli` |
| Import package | `wyrd_cli` |
| Installed command | `wyrd` |
| Initial version and tag | `0.1.0`, `v0.1.0` |
| Python release matrix | 3.12, 3.13, and 3.14 |
| Supported platform | Linux only |
| Test index | <https://test.pypi.org/project/wyrd-cli/> |
| Production index | <https://pypi.org/project/wyrd-cli/> |

The project uses Linux/POSIX facilities and does not support Windows or macOS.
The supported release matrix is Python 3.12, 3.13, and 3.14. Although package metadata
says `Requires-Python >=3.12`, each release is tested on those three Python versions. Confirm the normalized distribution name is still
`wyrd-cli` before first publication. A pending publisher does not reserve that name.
The Agent Skill under `skills/wyrd/` is distributed from the Git tag and is
intentionally absent from the wheel and source distribution. The maintainer-only
release skill lives under `.agents/skills/wyrd-release/`, where Pi discovers it only
from a trusted Wyrd worktree. Keeping it outside the package-level `skills/` directory
prevents global Wyrd package installations from exposing `/skill:wyrd-release`.

## Local release gate

Use repository-pinned uv from the release commit and run, from the repository root:

```console
uv run --locked --group release python scripts/release.py --artifact-dir dist/release --clear
```

This is the canonical build, metadata inspection, wheel/sdist smoke, and checksum
command. It recreates `dist/release`, requires exactly one wheel and one sdist, and
writes `dist/release.sha256`. See [the gate details](docs/releasing.md).

**Never reuse old local `dist/` artifacts.** Every TestPyPI or PyPI candidate must be
built from an empty output directory by the workflow at the intended commit. Local
artifacts are evidence only and must not be substituted for the GitHub Actions
handoff.

## Publishing workflows

| Target | Workflow file | Trigger | GitHub environment |
| --- | --- | --- | --- |
| TestPyPI | `.github/workflows/publish-testpypi.yml` | manual `workflow_dispatch` | `testpypi` |
| PyPI | `.github/workflows/publish-pypi.yml` | a GitHub Release becomes `published` | `pypi` |

Both workflows test Python 3.12/3.13/3.14 without OIDC permission, run the canonical
preflight in an unprivileged build job, upload the exact wheel, sdist, and checksum
manifest as an immutable Actions artifact, and download that artifact in a separate
publisher job. Only that final job gets `id-token: write`; it does not check out or
rebuild the project. PyPA's publishing action uses short-lived Trusted Publishing
credentials and creates PyPI publish attestations by default. There are no package
passwords or API tokens.

The TestPyPI workflow builds the commit selected when the manual run is dispatched and
rejects it unless that exact commit is contained in `main`. It has no index URL input
and can only upload to TestPyPI. The production workflow has no manual trigger: it
accepts only a published GitHub Release with a tag in the exact `vX.Y.Z` form, verifies
that the tag points to a commit in `main`, and requires the tag version to match package
metadata. Concurrent publication to the same target/version is serialized.

## One-time remote setup

Perform this only after both workflow files are on `main`.

### GitHub environments

In `debonzi/wyrd`, open **Settings > Environments** and create the exact, lowercase
environments `testpypi` and `pypi`. Do not add PyPI/TestPyPI secrets, tokens,
passwords, or credential variables to either environment or to the repository.

Recommended protection:

- `testpypi`: allow deployments only from the `main` branch. A required trusted
  maintainer reviewer is recommended for deliberate rehearsals; enable prevent
  self-review when the repository plan supports it.
- `pypi`: allow only selected tags matching `v*` (the workflow enforces the narrower
  `vX.Y.Z` rule), require at least one trusted maintainer reviewer, enable prevent
  self-review, and disable administrator bypass when available.

Environment reviewers must inspect the workflow summary before approval: selected tag
or ref, exact commit SHA, version, filenames, package SHA-256 values, and the GitHub
artifact digest must all match the intended release.

### Pending Trusted Publishers

TestPyPI and PyPI are separate services and accounts. Configure one pending GitHub
publisher on each service with these exact fields:

| Field | TestPyPI | PyPI |
| --- | --- | --- |
| PyPI project name | `wyrd-cli` | `wyrd-cli` |
| GitHub owner | `debonzi` | `debonzi` |
| GitHub repository | `wyrd` | `wyrd` |
| Workflow filename | `publish-testpypi.yml` | `publish-pypi.yml` |
| Environment | `testpypi` | `pypi` |

Use <https://test.pypi.org/manage/account/publishing/> for TestPyPI and
<https://pypi.org/manage/account/publishing/> for PyPI when the project does not yet
exist. If a project already exists under the maintainer account, add the same fields
on that project's **Publishing** settings instead. Workflow filenames and environment
names are identity claims: renaming either requires updating the publisher.

Enable 2FA on both account services and retain recovery codes in an approved offline
password/recovery system. Never put account credentials, API tokens, 2FA codes, or
recovery codes in GitHub, the repository, workflow logs, issues, or chat. The pending
publisher creates no project and reserves no name until its first successful upload.

## Version synchronization checklist

Before either rehearsal or production release, choose `X.Y.Z` and search the repository
for both the previous version and tag. Synchronize at least:

- `project.version` in `pyproject.toml`;
- `wyrd_cli.__version__` in `src/wyrd_cli/__init__.py`;
- the editable root package in `uv.lock` (regenerate it with the pinned uv);
- `metadata.version` and compatibility text in `skills/wyrd/SKILL.md`;
- README installation, support, and Agent Skill tag examples;
- the dated `CHANGELOG.md` entry (remove `Pending` for production);
- version-sensitive CLI, integration, and release tests; and
- the release tag `vX.Y.Z`.

Run the complete tests and canonical local gate, review `git diff --check`, commit the
release preparation, and merge it through the normal review process. The workflows
repeat the identity checks and refuse mismatches.

## TestPyPI rehearsal

A TestPyPI filename/version is immutable and cannot be overwritten. Use the intended
release version only when ready for the one meaningful rehearsal; if that TestPyPI
version already exists, prepare a new valid version rather than trying to replace it.

1. Confirm the release-preparation commit is merged into `main`, the `testpypi`
   environment and TestPyPI publisher are configured, and the local gate passes.
2. In GitHub **Actions**, choose **Publish wyrd-cli to TestPyPI**, select `main`, and
   run the workflow. The dispatch event freezes the selected commit SHA.
3. Review all verify/build jobs. Compare the summary's commit, version, two filenames,
   package SHA-256 values, and Actions artifact digest with the intended candidate.
4. Approve the `testpypi` environment deployment if reviewer protection is enabled.
5. After success, inspect the TestPyPI file list and attestations and compare its
   SHA-256 values as described below.

TestPyPI is not a trustworthy mirror of production dependencies. Never use an
unconstrained combination of TestPyPI and PyPI that can select same-named dependencies
from either service. One safe installation strategy is to resolve dependencies only
from production PyPI and then install the exact TestPyPI package without dependencies:

```console
uv venv /tmp/wyrd-testpypi
uv pip install --python /tmp/wyrd-testpypi/bin/python \
  --index-url https://pypi.org/simple \
  'pydantic>=2.12,<3' 'pyyaml>=6.0,<7' 'rich>=14.0,<15' 'typer>=0.21,<1'
uv pip install --python /tmp/wyrd-testpypi/bin/python \
  --no-deps --index-url https://test.pypi.org/simple 'wyrd-cli==X.Y.Z'
/tmp/wyrd-testpypi/bin/wyrd --version
/tmp/wyrd-testpypi/bin/wyrd --help
```

Exercise initialization, ticket creation/list/view, and JSON output in a disposable
workspace as well. Do not proceed to production if TestPyPI metadata, installation,
commands, checksums, or attestations differ from the candidate.

## Production tag and GitHub Release

Production is intentionally not dispatched by hand. After a successful rehearsal:

1. Confirm the exact reviewed release commit is on `main`, all synchronized versions
   equal `X.Y.Z`, the changelog is final, and the local gate passes from a clean tree.
2. Create a signed tag when signing is available (otherwise an annotated tag) named
   exactly `vX.Y.Z` at that commit. Do not use prefixes/suffixes, leading-zero numeric
   components, or prerelease text for this workflow.
3. Push that tag without moving or recreating it.
4. Create a draft GitHub Release for the existing tag, verify its target commit and
   release notes, then publish it. Publishing—not drafting—the Release triggers
   `.github/workflows/publish-pypi.yml`.
5. Review the verify/build summary and approve the protected `pypi` deployment only
   when the tag, SHA, version, filenames, package digests, and artifact digest match.
6. Wait for the PyPI action and attestation upload to complete; do not start another
   publication for the version.

A GitHub Release for a malformed tag or a tag whose version differs from package
metadata fails before publication. The publisher always downloads the artifact built
from the event's tagged commit and never builds from the current `main` tip.

## Post-upload verification

For the target service, inspect the project page, file list, metadata, `Requires-Python`,
dependencies, project links, license, and attestations. Compare index-reported SHA-256
values with the workflow checksum manifest:

```console
# Use test.pypi.org instead for a rehearsal.
curl -fsS "https://pypi.org/pypi/wyrd-cli/X.Y.Z/json" \
  | jq -r '.urls[] | "\(.digests.sha256)  \(.filename)"' \
  | sort
```

There must be exactly the expected pure-Python wheel and sdist, and both digest lines
must match the workflow summary/`release.sha256`. For stronger verification, download
the two URLs from the JSON response into a new empty directory and run `sha256sum` on
them. Do not compare against an old local `dist/` directory.

Finally install exactly `wyrd-cli==X.Y.Z` from the single intended index in a clean Linux
environment and repeat `wyrd --version`, `wyrd --help`, initialization, basic ticket
operations, and representative JSON output. Confirm the GitHub Release/tag and
changelog are visible and correct.

## Failures, immutability, and corrections

PyPI and TestPyPI artifacts are immutable. Never delete and replace, rebuild and retry,
or enable `skip-existing` to hide a mismatch. If a publish job fails, first inspect the
index and workflow artifact to determine whether neither, one, or both files arrived.
A rerun after a partial upload is dangerous: inspect PyPI state and compare exact
filenames and hashes before considering any rerun. Do not blindly rerun the publish job.
Escalate a partial production upload for a maintainer decision; never substitute a
fresh rebuild for the handed-off files.

For a bad but complete production release, normally yank it rather than delete it, add
a changelog notice, fix the defect, and publish a new patch version such as `0.1.1`
with a new tag and GitHub Release. Deletion is exceptional and requires an explicit
maintainer decision. A TestPyPI collision likewise requires a new version; it cannot be
fixed by overwriting the old files.
