# Engineering Standards

This repository is held to a strict, machine-enforced bar. Nothing here is
aspirational — every rule below is checked by a tool that fails the gate.

## The gates

| Gate | Tool | Contract |
| --- | --- | --- |
| Lint | `ruff check --select ALL` | **Every** ruff rule enabled; clean with zero `noqa` |
| Types | `basedpyright` + `pyrefly` | Strict mode; 0 errors / 0 warnings / 0 notes |
| Call shape | exactus `kwargs_typehints` | Keyword-only call sites, complete hints, no bare imports |
| Tests | `pytest` + `node --test` | 580+ Python tests, JS suites per plugin |
| Coverage | exactus `coverage_tests` | ≥90% overall, ≥80% per source file |
| Docs | exactus `docs` | Every public symbol carries Args/Returns/Raises docs |
| Readme | exactus `readme` | Every directory README + generated inventories valid |
| Perf | exactus `performance` | No pathological patterns |

Run the whole thing:

```bash
exactus --pipeline unified    # lints + coverage_tests + docs +
                              # kwargs_typehints + readme + performance
```

## The unusual rules

- **No suppressions, ever.** No `# noqa`, no `# type: ignore`, no
  `eslint-disable`, no ignore-config workarounds. Findings are fixed
  structurally or not at all.
- **No `assert` in tests.** `S101` is enforced; tests use the
  `expect`/`check`/`present` helper DSL (see [Testing](Testing)).
- **No bare imports.** `import x` is banned; everything is
  `from x import y`. `TYPE_CHECKING` blocks hold annotation-only imports.
- **Keyword-only call sites.** Positional calls to multi-arg functions are
  rejected mechanically — `quote(s=path)`, `run(args=cmd, check=True)`.
- **No `object`/`Any` annotations.** `CMP002`/`CMP003` ban them; the codebase
  uses precise aliases like `JsonValue`/`JsonObject` instead.
- **Immutable models.** Config and plan types are frozen dataclasses or
  TypedDicts; release `Action`s are data, not callbacks.
- **Determinism.** No ambient randomness in build/package output; the
  plugin packager is byte-reproducible.
- **Docstrings are API.** Missing `Args:`/`Returns:`/`Raises:` fail the
  docs gate — including nested helper functions and test fixtures.

## Conventions worth copying

- `@final` on leaf classes; `Final[...]` on constants.
- One class/function section per responsibility; `Constants` classes group
  module constants.
- `cast(typ="...", val=...)` for narrowing, never `Any`.
- Subprocess through the `Runner`/`CommandRunner` protocol — captured,
  timed-out, `BuildError` on failure.
- Errors carry context (`msg = f"..."` then raise), not silent swallows.

See [Testing](Testing) for the test-side rules and
[Architecture](Architecture) for the design rules they enforce.
