# Stationer Publish

`tools.stationer` is a provider-agnostic release control plane for a
single repository or a monorepo of independently versioned projects. Its
defining choice: **planning is separate from execution**. A release is
compiled into an immutable plan — inspectable, printable, dry-runnable,
journaled, resumable — before anything mutates the world.

## Model

- Each `Project` owns its path, current version, `version_files`,
  dependencies, build type and artifacts.
- Component-scoped tags by default (`demo/v1.2.3`) — no single-version
  monorepo assumption.
- `Planner` is side-effect-free except reading version files; it emits
  `Action` objects (`COMMAND` or `WRITE`, each carrying a description,
  command, path, content, cwd).
- `ReleaseEngine` executes actions, journals progress, and can resume an
  interrupted plan.

## `release.toml`

```toml
[repository]
provider = "github"
owner = "you"
name = "your-repo"
default_branch = "main"
visibility = "public"

[release]
tag_template = "{project}/v{version}"
pre_hooks  = [["{python}", "-m", "pytest", "tests", "-q"]]
post_hooks = []
publish_pypi = false
create_github_release = false   # this repo: CI creates the release

[projects.district]
path = "tools/district"
version_files = ["tools/district/product.py"]
```

Hooks are argv arrays; `{python}` expands to the interpreter.
`--skip-hooks` bypasses them. Version files are explicit rather than
guessed — plans are auditable, and a project may omit `version` to let its
first version file supply it.

## CLI

```text
python -m tools.stationer --config release.toml <command>
```

| Command | Effect |
| --- | --- |
| `status` | Report repository/project state |
| `show --project NAME` | Print one project's release metadata as JSON |
| `plan` | Compile and print actions without executing |
| `release` | Compile and execute a release plan |
| `publish --project NAME --artifacts PATH...` | Create (or with `--update`, upload to) the hosted release for the project's pushed tag |
| `repo-sync` | Create/update the configured repository via the provider |

Selection for `plan`/`release` (mutually exclusive): `--project` (repeatable),
`--all`, `--changed` (projects changed since their latest release tag).
`--dry-run` renders without executing; `--resume` continues a journaled
plan; `--no-publish` stops after tag push so CI can build between steps.

## Publishing an existing tag

```bash
python -m tools.stationer --config release.toml publish \
  --project district --artifacts dist/ extra.pkg [--update]
```

- Requires the tag to exist locally and files to attach — missing tags or
  empty inputs are reported, never half-published.
- `--artifacts` accepts files *and* directories (a directory contributes
  its direct files); everything attaches in a stable sorted order.
- Creation asks GitHub to generate notes starting from the **previous
  release tag of the same project** (`--notes-start-tag`); prerelease
  versions (`1.0.0-rc.1`) become GitHub prereleases.
- `--update` uses `gh release upload --clobber` on the existing release —
  same-named files are replaced.

## Semantics

- **Deterministic ordering** — topological sort over project dependencies.
- **Journal & resume** — every executed action is journaled; a crash or
  `--dry-run` never leaves ambiguous state.
- **Provider boundary** — the GitHub adapter is bundled; the engine only
  ever sees `Action`s, so new providers (or CI systems, GUIs, approval
  flows) plug in without touching planning.
- **Only `version_files` enter a release commit** — unrelated or untracked
  files can never sneak in.

## vs. semantic-release / changesets / lerna

Those are Node-centric and act inline. `stationer` is
language-agnostic, audit-first (plans are data), journaled, and resumable
by construction.

Next: the end-to-end flow in [Releasing](Releasing).
