# Release preflight

Wyrd releases are Linux-only. Run the complete local quality gate from the repository
root before handing artifacts to either TestPyPI or PyPI:

```console
uv run --locked --group release python scripts/release.py --artifact-dir dist/release --clear
```

`dist/release` is the caller-selected disposable output directory. `--clear` removes
and recreates that directory before building, so old files from the repository's
top-level `dist/` are never considered. The script refuses to use `dist/` itself as
the output directory.

The one command:

1. checks the PEP 621, package, lockfile, Agent Skill, README, changelog, and optional
   release-tag versions;
2. verifies the running uv version against `[tool.uv].required-version`;
3. runs `uv build --no-sources` into the empty directory and requires exactly
   `wyrd_cli-<version>.tar.gz` and `wyrd_cli-<version>-py3-none-any.whl`;
4. runs the locked Twine release tool as `twine check --strict`;
5. validates filenames, metadata, entry points, license data, dependencies, archive
   contents, forbidden files, and the intentional exclusion of `skills/wyrd`;
6. installs and exercises the wheel in an isolated environment outside the checkout;
7. builds a wheel from the fresh sdist with another clean environment, then installs
   and exercises that derived wheel; and
8. prints SHA-256 digests and records them in `dist/release.sha256`.

The smoke workflows run `wyrd --version`, `wyrd --help`, project initialization, ticket
creation/list/view, and a representative `--summary --json` check with sanitized CWD,
HOME, `PYTHONPATH`, locale, and color settings. Runtime dependencies are constrained by
`uv.lock`; no package is installed globally.

## CI release-tag interface

Local and non-production builds omit a tag. A production tag workflow must pass the
GitHub ref explicitly:

```console
uv run --locked --group release python scripts/release.py \
  --artifact-dir dist/release --clear --tag "$GITHUB_REF"
```

Only `v<project-version>` and `refs/tags/v<project-version>` are accepted. A tag for a
different version, a version without the `v` prefix, and branch refs are rejected.

When `GITHUB_OUTPUT` is set, the script emits `artifact_dir`, `wheel`, `sdist`,
`checksum_manifest`, and `version` outputs. GitHub Actions publication workflows should
invoke this exact interface once, upload those fresh files as the release candidate,
and publish the handed-off wheel and sdist without rebuilding them.

Do not upload artifacts merely because preflight passed. TestPyPI/PyPI publication,
tags, and GitHub Releases are separate authorized release steps.
