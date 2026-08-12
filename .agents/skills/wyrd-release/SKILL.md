---
name: wyrd-release
description: Prepares, validates, and publishes production wyrd-cli releases to PyPI with mandatory confirmations. Operational skill available exclusively through direct invocation with /skill:wyrd-release.
license: MIT
compatibility: Requires a trusted Linux checkout of the Wyrd repository, git, and the project-pinned uv version; remote steps require authorized GitHub access.
disable-model-invocation: true
---

# Wyrd release

Execute the workflow; do not merely present a plan. Respond in the user's language.
Accept the version as an argument, for example `/skill:wyrd-release 0.2.0`; without an
argument, propose a version and ask the user to confirm it.

## Fixed scope

- Work only in a trusted checkout of the repository whose PEP 621 distribution is
  `wyrd-cli` and whose intended remote has been confirmed by the user.
- Publish only to production PyPI through a GitHub Release and
  `.github/workflows/publish-pypi.yml`. Do not perform TestPyPI rehearsals or
  publications.
- Assume that environments, the Trusted Publisher, authentication, and protections
  are already configured. Do not try to configure them; if the workflow reports a
  problem with any of them, stop and identify the required correction.
- Accept only a stable `X.Y.Z` version without a `v` prefix, suffix, prerelease segment,
  or leading zeros. Use `vX.Y.Z` for the tag.
- Create an **annotated tag** only. Never create a signed, lightweight, or movable tag.
- Follow the current agent and project instructions when selecting a mechanism for
  GitHub operations. If no suitable integration or client is available, provide the
  exact GitHub UI action and wait for the user to perform it. Use `git` for branches,
  commits, tags, fetches, and pushes.
- Do not install or update system tools or GitHub clients. If `git` or the
  project-pinned `uv` version is unavailable, stop and tell the maintainer which
  command or action they need to perform.
- Never discard, overwrite, stash, or include pre-existing changes without specific
  authorization.

## Mandatory confirmations

A direct invocation authorizes the initial local inspection, not every mutation. Keep
the checkpoints below separate and wait for an unambiguous answer. A vague response
such as “continue” applies only to the checkpoint currently being presented.

1. **Preparation and remote scope:** confirm the version, UTC date, proposed branch,
   remote, and authorization to operate on that GitHub repository during this run
   before creating the branch or editing files.
2. **Commit and PR:** after showing the changed files, diff summary, and validation
   results, ask for authorization before creating the commit, pushing, and opening the
   PR.
3. **Merge:** show the PR number/URL, approvals, and checks; ask for authorization
   before merging. Never approve your own PR or bypass protections.
4. **Tag and draft:** at the exact `main` commit, show the SHA, version, notes, and local
   checksums; ask for authorization before creating/pushing `vX.Y.Z` and creating the
   draft GitHub Release.
5. **Final publication:** show the tag, SHA, version, and notes again. In the user's
   language, ask the equivalent of: “Do you confirm publishing GitHub Release
   `vX.Y.Z` now, triggering the irreversible publication of `wyrd-cli==X.Y.Z` to
   PyPI?” Remove the draft state only after explicit confirmation.

If an action is blocked by human review or a GitHub protection rule, give the user the
URL and exact instruction, then stop and wait. Do not simulate approval.

## 1. Initial inspection and version selection

Before any mutation:

1. Locate the root with `git rev-parse --show-toplevel` and work from that directory.
2. Read `pyproject.toml`, `CHANGELOG.md`, `src/wyrd_cli/__init__.py`,
   `skills/wyrd/SKILL.md`, `README.md`, `RELEASING.md`, and `docs/releasing.md`.
3. Locally confirm the `wyrd-cli` name, current version, branch, commit, remote, and
   `git status --porcelain`.
4. If the checkout is dirty, list the changes and stop. Do not combine release
   preparation with pre-existing work.
5. Validate the version argument, or propose a patch/minor/major bump based on the
   contents of `Unreleased`. Explain the recommendation in one sentence, but leave the
   decision to the user.
6. Obtain the date with `date -u +%F` and present the first checkpoint, including the
   exact remote owner/repository. Do not access the remote before that authorization.

After confirmation, verify the remote and update references without rewriting history:

```bash
git fetch origin main --tags
git switch -c release/vX.Y.Z origin/main
```

If the branch or tag already exists locally or remotely, stop and report the collision.
Do not reuse it automatically.

## 2. Local preparation

Synchronize the following without a blind global replacement:

- `project.version` in `pyproject.toml`;
- `wyrd_cli.__version__` in `src/wyrd_cli/__init__.py`;
- the editable root package in `uv.lock`, regenerated with the project-pinned `uv`;
- `metadata.version` and the `wyrd-cli MAJOR.MINOR.x` family in
  `skills/wyrd/SKILL.md`;
- current-version statements and examples in `README.md`;
- test expectations that genuinely represent the current version;
- `CHANGELOG.md`: preserve an empty `## Unreleased` heading and move its contents into
  `## X.Y.Z - YYYY-MM-DD`.

First find every occurrence of the previous version and tag. Classify each occurrence
before editing it: historical references, such as “Initial version” in `RELEASING.md`,
do not change merely because a new release is being prepared.

Run `uv --version` before `uv lock`. If it does not match
`[tool.uv].required-version`, stop; do not install another version on your own.

Review the complete diff. Unexpected files or changes unrelated to release identity
require an explanation and confirmation, not automatic inclusion.

## 3. Local gate

Run from the repository root:

```bash
uv run --locked pytest
uv run --locked --group release python scripts/release.py \
  --artifact-dir dist/release --clear
git diff --check
git status --short
```

The preflight must produce exactly one wheel, one sdist, and a
`dist/release.sha256` file with two entries. Report the version, filenames, and
SHA-256 values. These artifacts are local evidence only; the production workflow will
rebuild the exact candidate from the tag.

If any test or gate fails, stop, diagnose it, and propose a correction. Do not reduce
test coverage, ignore failures, or continue to the commit.

Present the second checkpoint with:

- version and branch;
- list of changed files;
- changelog and diff summary;
- test and preflight results;
- local filenames and checksums;
- proposed commit: `Prepare release X.Y.Z`.

## 4. Commit, PR, and merge

After authorization, add only the previously reviewed files—never use `git add -A`—and
create the commit:

```bash
git commit -m "Prepare release X.Y.Z"
git push -u origin release/vX.Y.Z
```

Create the PR to `main` using the available GitHub integration, including the version,
changelog, validation commands, and local checksums in the body. Show the URL and
monitor its checks. If no integration is available, provide the exact base branch,
head branch, title, body, and GitHub UI action, then wait for the user to create it.

When all Python 3.12, 3.13, and 3.14 checks pass and required reviews are satisfied,
present the merge checkpoint. If the repository's normal merge strategy is unclear,
ask the user; do not choose merge, squash, or rebase by assumption. After
authorization, merge it using the available GitHub integration without bypassing
protections. If automation is unavailable or the user must merge through the
interface, identify the exact action and wait.

After the merge:

1. update `main` with `git fetch origin main --tags`, `git switch main`, and
   `git pull --ff-only`;
2. confirm a clean tree, synchronized version, and a commit contained in `origin/main`;
3. rerun the canonical gate at the exact `main` commit, now also validating the
   intended identity with `--tag vX.Y.Z`;
4. confirm that `vX.Y.Z` does not exist locally or in `origin`.

## 5. Annotated tag and draft GitHub Release

Extract the `X.Y.Z` changelog section into a temporary file outside the checkout. Show
the notes, exact `main` SHA, tag, and checksums from the gate executed at that commit.
Then present the fourth checkpoint.

After authorization:

```bash
git tag -a vX.Y.Z <exact-sha> -m "Release X.Y.Z"
git show --no-patch --format=fuller vX.Y.Z
git push origin refs/tags/vX.Y.Z
```

Verify that the remote tag, including its referenced object, resolves to the approved
SHA. Never move, delete, or recreate a pushed tag.

Create the GitHub Release as a draft for the existing tag using the available GitHub
integration. Require the equivalent of draft mode, existing-tag verification, and the
temporary notes file. Then inspect it and show its URL, title, notes, tag, and target.
If automation is unavailable, provide the exact GitHub UI fields and action, then wait
for the user to create the draft before inspecting it.

## 6. PyPI publication

Present the final checkpoint exactly as specified above. After explicit confirmation,
change the draft GitHub Release to published using the available GitHub integration.
If automation is unavailable, give the user the exact draft Release URL and publication
action, then wait for confirmation that it has been published.

This must trigger `.github/workflows/publish-pypi.yml`. Locate the run by workflow and
SHA, inspect it, and monitor it through the available GitHub integration or the Actions
UI.

When the protected `pypi` job is waiting for approval, show the run URL and ask the
user to compare the following in the summary:

- tag and commit;
- version;
- wheel and sdist filenames;
- SHA-256 values for both packages;
- Actions artifact name and digest.

Tell the user to approve the `pypi` environment only if everything matches, and wait.
Do not try to bypass or grant that approval.

## 7. Post-publication verification

After the workflow completes successfully:

1. query `https://pypi.org/pypi/wyrd-cli/X.Y.Z/json`;
2. confirm metadata, `Requires-Python`, dependencies, links, wheel, sdist, and
   attestations;
3. download the workflow handoff artifact into a new temporary directory using the
   available GitHub integration and compare published hashes with that handoff's
   `release.sha256`—never with an old local `dist/` directory; if automated download
   is unavailable, give the user the exact run/artifact and destination, then wait;
4. create a temporary Linux environment, install exactly `wyrd-cli==X.Y.Z` exclusively
   from PyPI, and exercise `wyrd --version`, `wyrd --help`, initialization, ticket
   creation/list/view, and representative JSON output;
5. do not alter the user's global installation, and remove only temporary files created
   by this run.

Finish with the version, tag, SHA, GitHub Release URL, PyPI URL, workflow ID/URL,
published hashes, completed verifications, and any remaining action.

## Failures and immutability

- On any failure, identify the phase, preserve evidence, and stop. Do not retry blindly.
- If publication fails, first inspect PyPI and the workflow artifact to determine
  whether neither, one, or both files arrived. Do not rerun the publishing workflow
  without a new explicit maintainer decision.
- Never use `skip-existing`, never replace a published file, and never rebuild an
  artifact to complete a partial upload.
- For a complete but defective release, propose yanking it and publishing a new patch
  version; do not delete the release or project without an explicit decision.
