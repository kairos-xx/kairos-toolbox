# Releasing — End to End

How a District Terminal release actually flows through this repo's own tooling.

## The pipeline

```text
district --make-release patch
        │
        ▼
 stationer plan/release          ← release.toml
   · run pre_hooks (pytest gate)
   · bump tools/district/product.py  (version_files)
   · stage + commit only version files
   · tag  district/vX.Y.Z
   · push
        │
        ▼
 .github/workflows/district-release.yml    ← CI on the tag
   · stationer --format pyz      → per-platform .pyz archives
   · lint + tests
        │
        ▼
 stationer publish               ← create the hosted release
   --project district             · --generate-notes since previous tag
   --artifacts dist/ [more paths]      · --update to re-upload assets
```

## Roles

| Step | Tool | Why |
| --- | --- | --- |
| Version bump, tag, push | [Stationer Publish](Stationer-Publish) | Immutable plan, journaled, hooks gate on tests |
| Per-platform archives | [Stationer](Stationer-Builder) | `.pyz` is platform-specific (compiled deps) |
| Hosted release | `stationer publish` | CI-built artifacts attached to the pushed tag |

## Key properties

- **Single version source** — `product.py`; `release.toml` points at it via
  `version_files`, and the builder's `version_file` reads the same file.
- **Tests gate the release** — `pre_hooks` runs the suite before the bump.
- **Local run never creates the release** — `create_github_release = false`;
  pushing the tag is the hand-off to CI.
- **Resumable** — if execution stops mid-plan, `--resume` continues the
  journal instead of restarting.
- **Self-updates consume this** — `district --update` downloads exactly the
  `district-X.Y.Z-<platform>.pyz` assets this pipeline produces.

## Maintainer cheat sheet

```bash
# Dry-run the whole plan first
district --make-release minor --dry-run

# Verify + bump + tag + push (pre_hooks run tests first)
district --make-release minor

# Or drive the engine directly
python -m tools.stationer --config release.toml plan --project district

# After CI built artifacts — attach them to the pushed tag's release
python -m tools.stationer --config release.toml publish \
  --project district --artifacts dist/

# Re-upload / fix assets on an existing release
python -m tools.stationer --config release.toml publish \
  --project district --artifacts dist/ --update
```

## PyPI and the wiki

District Terminal's `pyproject.toml` declares `bowman`, a sibling
distribution — `pip install district` only resolves if Bowman is on PyPI
too. `--format pypi` builds every `local_dependencies` entry **first**, then
the project, all from staged copies:

```bash
python -m tools.stationer --project tools/district \
  --manifest tools/district/templates/stationer.toml \
  --format pypi --output build/pypi
python -m tools.stationer.upload build/pypi
```

`stationer --publish` also synchronizes a `wiki/` directory at the
release-config root into the repo's GitHub Wiki (`<repo>.wiki.git`) after
the release plan runs — this page's source.

See also: [Stationer Publish](Stationer-Publish) ·
[Stationer](Stationer-Builder) · [Engineering Standards](Engineering-Standards)
