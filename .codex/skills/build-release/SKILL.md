---
name: build-release
description: Build, audit, smoke-test, publish, monitor, or verify flat2vr Python releases. Use when the user invokes $build-release or /build-release; asks for local release artifacts or a release candidate; requests a version bump, tag, PyPI publication, or GitHub Release; or asks whether an exact flat2vr version is live.
---

# Build Release

Use the repository-owned release helper and trusted-publishing workflow. Keep a
local candidate separate from external publication: building is reversible;
pushing a `v<version>` tag publishes to PyPI.

Treat an unqualified `$build-release` invocation as authorization to complete
the publication flow. Use the local-candidate flow only when the user explicitly
asks for artifacts, a candidate, a dry run, validation-only work, or no
publication.

Use normal project-owned PEP 440 versions. Treat the checked-in version as
pending when it has no `v<version>` tag and no PyPI files. Otherwise select the
next unused patch version; increment an existing `aN`, `bN`, `rcN`, or `.devN`
suffix instead. Honor an exact unused version requested by the user.

Keep the version identical in `pyproject.toml`, `src/flat2vr/__init__.py`, and
the root `flat2vr` entry in `uv.lock`.

## Build a local candidate

1. Inspect the repository without discarding or committing user changes:

```bash
git status --short --branch
python3 .codex/skills/build-release/scripts/release_build.py check-version
python3 .codex/skills/build-release/scripts/release_build.py prepare-version
```

A dirty worktree may produce a diagnostic candidate, but it is not eligible for
publication. Do not write an automatic version bump onto a dirty tree.

2. Run the locked source gates:

```bash
uv lock --check
uv sync --frozen
uv run --frozen python -m compileall -q src
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen flat2vr --help
```

3. Confirm the selected version is unused and build into a fresh directory:

```bash
python3 .codex/skills/build-release/scripts/release_build.py \
  check-pypi --version <version>
python3 .codex/skills/build-release/scripts/release_build.py build \
  --version <version> --out-dir dist/release-v<version>
```

The helper requires exactly `flat2vr-<version>-py3-none-any.whl` and
`flat2vr-<version>.tar.gz`. It audits metadata, the console entry point, all
bundled Docker/container/model-lock resources, contamination, filenames, and
the sdist layout. It then installs the wheel without dependencies into an
isolated environment, invokes the CLI, verifies the default backend, checks the
installed container context, and prints SHA-256 digests.

4. Report both artifact paths, hashes, selected version, and completed gates.
Preserve failed artifacts and the exact failure for diagnosis.

## Publish a release

Require all of the following before tagging:

- a clean `main` worktree synchronized with `origin/main`;
- no unpublished or incoming commits;
- consistent version metadata and an unused PyPI version and tag;
- a passing local candidate built from the exact commit to tag;
- `.github/workflows/release.yml` matching the contract below; and
- a PyPI Trusted Publisher for owner `tsilva`, repository `flat2vr`, workflow
  `release.yml`, and GitHub environment `pypi`.

Do not create or switch branches, move an existing tag, manually upload with
Twine or `uv publish`, print credentials, or put a PyPI token on a command line.

1. Fetch release state and stop on dirtiness or divergence:

```bash
git fetch origin main --tags
git status --short --branch
git log --oneline origin/main..HEAD
git log --oneline HEAD..origin/main
```

2. Select and, when needed, transactionally write the version:

```bash
python3 .codex/skills/build-release/scripts/release_build.py \
  prepare-version --write
```

For an exact user-selected version, add `--to <version>`. Re-run the version,
lock, source, PyPI, and candidate gates. If preparation changed metadata, commit
exactly `pyproject.toml`, `src/flat2vr/__init__.py`, and `uv.lock` as
`Release v<version>` before building the final candidate.

3. Create an annotated tag only after the candidate passes, then atomically
push the current branch and tag:

```bash
git tag -a v<version> -m "Release v<version>"
git push --atomic origin HEAD v<version>
```

4. Resolve the tag commit and monitor only its matching workflow:

```bash
release_sha="$(git rev-list -n 1 v<version>)"
gh run list --workflow release.yml --commit "$release_sha" --limit 5 \
  --json databaseId,status,conclusion,event,headBranch,headSha,url
gh run watch <run-id> --exit-status
```

If the run fails, inspect only failed logs with
`gh run view <run-id> --log-failed`. Do not improvise a manual upload.

5. After the workflow succeeds, verify both PyPI files and the GitHub Release:

```bash
python3 .codex/skills/build-release/scripts/release_build.py \
  wait-pypi --version <version>
gh release view v<version> --json url,tagName,assets
```

Do not report completion until PyPI contains both exact distributions and the
GitHub Release exists for the tag.

## Workflow contract

- `workflow_dispatch` validates and uploads audited workflow artifacts but does
  not publish.
- A pushed `v*` tag must match the project version, pass the source gates, build
  and audit one universal wheel and one sdist, and upload only those artifacts.
- The `publish` job uses the protected `pypi` environment and OIDC Trusted
  Publishing; it never uses a repository token.
- The GitHub Release is created only after PyPI publication succeeds.

## Final response

For a local candidate, lead with the artifact directory and report both files,
SHA-256 digests, version, and gates. For publication, lead with
`https://pypi.org/project/flat2vr/<version>/` and report the tag, commit,
workflow URL and conclusion, GitHub Release URL, and both distribution names.
On failure, report the exact command or job and the next safe recovery action.
