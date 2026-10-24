# Testing

## Layout

```text
tests/
  tools/district/        District Terminal suite (installer, update,
                              storage, URI handler, gate coverage, pyz)
  tools/district/js/     Dashboard web-asset Node tests
  tools/stationer/      Builder, manifest, pyz, local deps
  tools/stationer/      Config, planner, engine, providers, CLI
  plugins/<name>/js/          Node test files per plugin
plugins/devin_remote_control/src/test_original/
                              Extension-side Node test suites
```

Run them:

```bash
python -m pytest -q                                   # full Python suite
python -m pytest tests/tools/district -q         # one area
node --test "tests/plugins/devin_remote_control/js/*.test.*"
node --test "tests/tools/district/js/*.test.mjs"
cd plugins/devin_remote_control/src && node --test test_original/*.test.mjs
```

## The assertion DSL

`assert` is banned (`S101`). Every test package has a `helpers.py`
exporting:

| Helper | Use |
| --- | --- |
| `expect(actual=..., expected=...)` | Equality assertion |
| `check(condition=..., reason=...)` | Boolean assertion with a failure message |
| `present(value=...)` | Unwrap `Optional` — returns the value or fails |

```python
expect(actual=running_archive(), expected=archive)
check(condition="demo/__init__.py" in names, reason=str(object=names))
layout: Layout = present(value=manifest_layout(home=tmp_path))
```

## Faking the world

- **`RecordingRunner` / `FakeRunner`** — `CommandRunner`-compatible fakes
  that record commands and fabricate the artifact files each builder
  expects (`handlers` like `linux_handler(version=...)`).
- **`PackageRunner`** — runs real commands but intercepts `pip` to stage
  the package by copy, so `.pyz` builds are exercised end-to-end without
  network.
- **Subprocess isolation** — when pytest's package layout shadows the real
  package (e.g. `tests/tools/district` vs `district`), the code
  under test runs in a subprocess with an explicit `sys.path`.
- **`monkeypatch` + typed stubs** — patched callables are `def`s with full
  signatures, not lambdas; strict typing applies to tests too.

## Coverage contract

exactus `coverage_tests` enforces **≥90% overall and ≥80% per file** across
Python *and* the JavaScript lane, and it fails on untested meaningful
sources — dev tooling like `tools/*.mjs` needs tests too. Measured through
`pytest-cov` and Node's test coverage.

## Deterministic-build tests

`tests/plugins/devin_remote_control/js/tools.test.js` runs `package.mjs`
twice and asserts identical SHA-256 for every emitted artifact —
reproducibility is a tested property, not a hope.

## What "meaningful" means

Coverage tests target behavior, not lines: error branches, resume paths,
tag guards, checksum verification, and schema-recovery semantics are all
exercised — e.g. corrupt/older-schema databases are set aside and rebuilt
while newer schemas are left untouched.
