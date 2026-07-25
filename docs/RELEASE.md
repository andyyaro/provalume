# Release procedure

## Before anything

Every gate must pass. CI runs the same commands; running them locally first is
faster than finding out from a red tag.

```sh
uv run ruff check src tests
uv run mypy
uv run bandit -q -c pyproject.toml -r src
uv run pip-audit
uv run pytest tests
uv run provalume eval
uv run python docs/design/contrast_check.py
```

Then check the things a gate cannot:

- [ ] `CHANGELOG.md` has an entry for this version, written for a reader who has
      not followed development
- [ ] `LIMITATIONS.md` still tells the truth
- [ ] `evals/results/baseline/results.json` regenerated if behaviour changed
- [ ] no `TODO`, `FIXME`, or `XXX` left in `src/`
- [ ] version in `pyproject.toml` matches the tag you are about to create

## Cut it

```sh
# 1. Version and changelog, in one commit
$EDITOR pyproject.toml CHANGELOG.md
git commit -am "release: v0.1.0"

# 2. Annotated tag. Never lightweight, never moved, never reused.
git tag -a v0.1.0 -m "provalume v0.1.0 — verified, git-aware memory for agents"

# 3. Verify the distributions before pushing anything
uv build
ls dist/                       # provalume-0.1.0-py3-none-any.whl, .tar.gz
uv run twine check dist/*      # or: uvx twine check dist/*

# 4. Install each distribution in a clean environment and smoke-test it
uv run --isolated --with dist/provalume-0.1.0-py3-none-any.whl provalume --version
uv run --isolated --with dist/provalume-0.1.0.tar.gz provalume demo

# 5. Push
git push origin main
git push origin v0.1.0
```

## Publishing

The tag push triggers `.github/workflows/publish-to-pypi.yml`, which runs the
gates again, builds both distributions, tests them, and publishes through **PyPI
Trusted Publishing over OIDC** in a job that is the only place `id-token: write`
is granted.

There is no long-lived API token anywhere. That is the point: a token in a secret
store is a token that can leak.

The publish job runs in the protected `pypi` environment. First release requires
a pending publisher configured on PyPI:

| Field | Value |
|---|---|
| Project | `provalume` |
| Owner | `andyyaro` |
| Repository | `provalume` |
| Workflow | `publish-to-pypi.yml` |
| Environment | `pypi` |

## Verify in a clean environment

```sh
uv tool install provalume
provalume --version
provalume doctor
provalume demo
provalume --help
provalume serve-mcp --help
```

Confirm all five agree: `pyproject.toml`, the Git tag, the GitHub release, the
PyPI release, and `provalume --version` from a fresh install. If any disagrees,
stop and reconcile before announcing.

## Rules that do not bend

- **Never move or reuse a tag.** If a release is wrong, yank it on PyPI and
  release a new patch version. A moved tag makes every prior verification a lie.
- **Never overwrite a published version.** PyPI forbids it, and the reason is
  the same.
- **Never publish from a local machine.** OIDC from CI or not at all.
- **Never weaken a gate to get a release out.** A red gate is information.

## After

- [ ] GitHub release created from the tag, with the changelog entry as its body
- [ ] Attestations present on the PyPI release
- [ ] Fresh-install verification actually run, not assumed
- [ ] Open issues for anything deferred during the release
