# Releasing — End to End

How a Lake Terminal release actually flows through this repo's own tooling.

## The pipeline

```text
lake --make-release patch
        │
        ▼
 release_publish plan/release          ← release.toml
   · run pre_hooks (pytest gate)
   · bump tools/lake_terminal/lake_terminal/product.py  (version_files)
   · stage + commit only version files
   · tag  lake-terminal/vX.Y.Z
   · push
        │
        ▼
 .github/workflows/lake-release.yml    ← CI on the tag
   · release_builder --format pyz      → per-platform .pyz archives
   · lint + tests
        │
        ▼
 release_publish publish               ← create the hosted release
   --project lake-terminal             · --generate-notes since previous tag
   --artifacts dist/ [more paths]      · --update to re-upload assets
```

## Roles

| Step | Tool | Why |
| --- | --- | --- |
| Version bump, tag, push | [Release Publish](Release-Publish) | Immutable plan, journaled, hooks gate on tests |
| Per-platform archives | [Release Builder](Release-Builder) | `.pyz` is platform-specific (compiled deps) |
| Hosted release | `release_publish publish` | CI-built artifacts attached to the pushed tag |

## Key properties

- **Single version source** — `product.py`; `release.toml` points at it via
  `version_files`, and the builder's `version_file` reads the same file.
- **Tests gate the release** — `pre_hooks` runs the suite before the bump.
- **Local run never creates the release** — `create_github_release = false`;
  pushing the tag is the hand-off to CI.
- **Resumable** — if execution stops mid-plan, `--resume` continues the
  journal instead of restarting.
- **Self-updates consume this** — `lake --update` downloads exactly the
  `lake-terminal-X.Y.Z-<platform>.pyz` assets this pipeline produces.

## Maintainer cheat sheet

```bash
# Dry-run the whole plan first
lake --make-release minor --dry-run

# Verify + bump + tag + push (pre_hooks run tests first)
lake --make-release minor

# Or drive the engine directly
python -m tools.release_publish --config release.toml plan --project lake-terminal

# After CI built artifacts — attach them to the pushed tag's release
python -m tools.release_publish --config release.toml publish \
  --project lake-terminal --artifacts dist/

# Re-upload / fix assets on an existing release
python -m tools.release_publish --config release.toml publish \
  --project lake-terminal --artifacts dist/ --update
```

## PyPI and the wiki

Lake Terminal's `pyproject.toml` declares `arrow-local-uri`, a sibling
distribution — `pip install lake-terminal` only resolves if Arrow is on PyPI
too. `--format pypi` builds every `local_dependencies` entry **first**, then
the project, all from staged copies:

```bash
python -m tools.release_builder --project tools/lake_terminal \
  --manifest tools/lake_terminal/release-builder.toml \
  --format pypi --output build/pypi
python -m tools.release_publish.upload build/pypi
```

`release_builder --publish` also synchronizes a `wiki/` directory at the
release-config root into the repo's GitHub Wiki (`<repo>.wiki.git`) after
the release plan runs — this page's source.

See also: [Release Publish](Release-Publish) ·
[Release Builder](Release-Builder) · [Engineering Standards](Engineering-Standards)
