# Releasing

How a release of this integration (`hass-comap`) is made. The library it depends on, `pycomap`, is
released separately from its own repository; see the "Releasing" section of that repo's
`CONTRIBUTING.md`.

## How the automation works

`.github/workflows/release.yml` runs on every push to `main` (or manually via *Run workflow*):

1. Installs with `uv sync --frozen`, then runs lint (`ruff`), type check (`ty`) and tests.
2. Reads `version` from `pyproject.toml` and derives the tag `v<version>`.
3. **If the tag already exists on `origin`, it stops here.** Merging to `main` without bumping the
   version is therefore harmless: no tag, no release.
4. Otherwise it creates and pushes the tag and a GitHub Release with auto-generated notes. Versions
   with a PEP 440 suffix (`1.1.0rc1`, `a1`, `b1`) are marked as **pre-release**.

Nothing is published to PyPI; HACS users receive the new GitHub Release.

Pull requests and pushes are also checked by `tests.yml` (pytest) and `validate.yml` (HACS action
and hassfest, also run nightly).

If a run fails *after* the tag was created, re-running it will not create the release, because
the tag now exists and the later steps are skipped. Fix the cause, delete the remote tag
(`git push origin --delete vX.Y.Z`, plus the GitHub Release if one exists) and re-run. Pushing a tag
by hand does nothing: the workflow only runs on pushes to `main` and manual dispatch.

## Branches

- `dev`: day-to-day work.
- `main`: releases only, updated by a PR from `dev`. Merging that PR *is* the release.

## Choosing the version (semver)

- **Patch** (`x.y.Z`): bug fixes only.
- **Minor** (`x.Y.0`): new features or visible behaviour changes (new options, new flows).
- **Major** (`X.0.0`): breaking changes, e.g. entity IDs or config entry data that need migration.

## Steps

Work on `dev`.

1. If this release needs a newer `pycomap`, release that first and wait until it is on PyPI.
   Then raise the floor in `pyproject.toml`, e.g. `"pycomap>=2.4.0"`.
2. Bump this project's `version` in `pyproject.toml`.
3. Refresh the lockfile (needs the new `pycomap` on PyPI; retry after a minute if uv can't find it):
   ```sh
   uv lock
   ```
4. Commit. The `sync-meta` pre-commit hook (`scripts/sync_meta.py`) rewrites, from `pyproject.toml`
   and `uv.lock`:
   - `manifest.json`: `version`, and `requirements` pinned to the **locked** `pycomap==X.Y.Z`
   - `hacs.json`: minimum Home Assistant version
   - `.python-version`

   The hook exits non-zero when it changed something, so the first commit attempt fails. Stage the
   updated files and commit again. Do **not** edit `manifest.json` by hand.
5. Run the full checks:
   ```sh
   just check && just test
   ```
6. Push `dev`, open a PR `dev` → `main`, wait for CI (tests, HACS, hassfest), merge.
7. Watch the **Release** run; confirm the tag and GitHub Release exist.

## Testing against an unreleased `pycomap`

To develop against the sibling `../pycomap` checkout without publishing it:

```sh
just local-lib    # editable install of ../pycomap into .venv (local only)
just remote-lib   # back to the version pinned in uv.lock
```

`pyproject.toml` and `uv.lock` are never touched; the switch is a gitignored marker file
(`.use-local-pycomap`) plus an editable install in `.venv`. Run `just remote-lib` before the final
checks so the tests run against what is actually published.

## Checklist

- [ ] `pycomap` floor raised in `pyproject.toml` (if needed) and the new version is on PyPI
- [ ] `version` bumped, `uv lock` run
- [ ] `manifest.json` shows `pycomap==<locked version>` (written by the hook, not by hand)
- [ ] `just check && just test` pass with `just remote-lib` active
- [ ] PR `dev` → `main` merged, **Release** workflow green, tag and GitHub Release created
