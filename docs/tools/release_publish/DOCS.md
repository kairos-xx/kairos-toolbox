# Release Publish — Extended Documentation and API Reference

`tools.release_publish` is a provider-agnostic release control plane for a
single repository or a monorepo containing independently versioned projects.
It deliberately separates **planning** from **execution**: a release is first
compiled into an immutable plan; the same plan can be inspected, rendered,
run as a dry-run, executed, journaled, and resumed.

## Contents

- [Architecture and configuration](#architecture-and-configuration)
- [`release.toml` reference](#releasetoml-reference)
- [CLI reference](#cli-reference)
- [Planning and execution semantics](#planning-and-execution-semantics)
- [Python API](#python-api)
- [Provider boundaries](#provider-boundaries)
- [Monorepo semantics](#monorepo-semantics)
- [Hooks](#hooks)
- [Security](#security)
- [Extending](#extending)

## Architecture and configuration

## Model

A repository contains zero or more independently versioned `Project` objects.
Each project owns its path, current version, version-bearing files, dependencies,
build type, and release artifacts. Releases use component-scoped tags by
default (`component/v1.2.3`), avoiding the single-version assumption common in
monorepo tooling.

The `Planner` is intentionally side-effect free except reading version files.
It compiles intent into immutable `Action` objects. `ReleaseEngine` executes
those actions. This gives callers a stable Python API for policy engines, GUIs,
CI systems, approval workflows, and alternate providers.

## `release.toml` reference

### `[repository]` — required

| Key | Type | Description |
| --- | --- | --- |
| `provider` | string | Adapter name; `"github"` is bundled. |
| `owner` | string | Repository owner/organization. |
| `name` | string | Repository name. |
| `default_branch` | string | Default branch for creation/sync (e.g. `main`). |
| `visibility` | string | `public` or `private` for repository creation. |
| `description` | string | Repository description applied by `repo-sync`. |

### `[release]` — required

| Key | Type | Description |
| --- | --- | --- |
| `tag_template` | string | Component tag, e.g. `"{project}/v{version}"`. |
| `root_tag_template` | string | Optional root tag, e.g. `"v{version}"`. |
| `pre_hooks` | list | argv arrays run before version bumps. |
| `post_hooks` | list | argv arrays run after the release steps. |
| `publish_pypi` | bool | Include `twine upload` actions for Python distributions. |
| `create_github_release` | bool | Include `gh release create` actions. |

### `[projects.<name>]` — one table per component

| Key | Type | Description |
| --- | --- | --- |
| `path` | string | Project directory relative to the repository root. |
| `version` | string | Current version literal. |
| `version_files` | list | Files whose `version = "X.Y.Z"` literal is rewritten; exactly one must contain the current version. |
| `python_distribution` | bool | Build/upload as a Python package. |
| `distribution_name` | string | Distribution name when it differs from the project name. |
| `depends_on` | list | Names of projects that must release first. |
| `artifacts` | list | Extra files uploaded to the GitHub release. |

The bundled repository `release.toml` configures the `lake-terminal` project
and documents itself inline; `init` generates a starter file.

## CLI reference

```bash
python -m tools.release_publish [--config release.toml] <command>
```

| Command | Description |
| --- | --- |
| `init` | Write a starter `release.toml` to `--config`. |
| `status` | Print each configured project and version. |
| `show --project NAME` | Print one project's release metadata as JSON. |
| `plan` | Compile a release plan and print actions without executing. |
| `release` | Compile and execute a release plan. |
| `publish --project NAME --artifacts DIR` | Create the hosted release for the project's already-pushed tag, attaching every file in `DIR` ([details](#publishing-an-existing-tag)). |
| `repo-sync` | Create/update the configured repository via the provider. |

Selection options for `plan`/`release` (mutually exclusive):

| Option | Description |
| --- | --- |
| `--project NAME` | Repeatable explicit project selection. |
| `--all` | All configured projects. |
| `--changed` | Projects changed since their own latest tag. |

Version options for `plan`/`release` (mutually exclusive):

| Option | Description |
| --- | --- |
| `--bump major\|minor\|patch` | Semantic bump (default `patch`). |
| `--version X.Y.Z` | Explicit target version; must exceed the latest tag. |

Other flags:

| Option | Description |
| --- | --- |
| `--no-publish` | Omit PyPI/GitHub release actions from the plan. |
| `--skip-hooks` | Omit `pre_hooks`/`post_hooks` actions. |
| `--dry-run` | (`release`, `repo-sync`) print without side effects. |
| `--resume` | (`release`) skip actions already in the journal. |

Errors exit with status `2`.

## Planning and execution semantics

- `ReleaseEngine.plan` produces `PlannedProject` + `Action` tuples: clean-tree
  preflight checks, `pre_hooks`, version-file rewrites, `git add`/`commit` of
  the version files only, tagging, build/publish steps, `post_hooks`.
- Tag guards: an existing `{project}/v{version}` tag aborts planning; an
  explicit `--version` must be newer than the latest matching tag.
- `{python}` inside hooks expands to `sys.executable`; hooks run as argv
  tuples from the project directory without a shell.
- `execute` runs actions sequentially; `--dry-run` prints instead.
  `WRITE` actions modify files only outside dry-run.
- A journal at `<root>/.release-publish-journal.jsonl` records completed
  action indexes; `--resume` skips them so an interrupted release can
  continue deterministically.
- `changed_projects` compares `git status`/`diff` output with each project's
  latest tag.

## Python API

- `load_config(path)` → `Config` — parse and validate `release.toml`.
- `Config` — `root`, `repository` (`RepositoryConfig`), `release`
  (`ReleaseConfig`), `projects`; `project(name)` lookup.
- `Version.parse("X.Y.Z")` → strict SemVer (`major`, `minor`, `patch`,
  `prerelease`, `build`); `bump(Bump)`; ordering is version-aware.
- `Bump` — `major`, `minor`, `patch`.
- `read_version(text)` → `(version, count)`; `replace_version(text, old,
  new)` → rewritten text; both refuse ambiguous version literals.
- `Planner(config, github, pypi).plan(names, bump, explicit_version,
  publish, skip_hooks)` → `ReleasePlan` with dependency-ordered
  `PlannedProject` items and `Action` sequences.
- `Action` — `kind` (`ActionKind.COMMAND`/`WRITE`), `description`,
  `command`, `path`, `content`, `cwd`, `secret_indexes`.
- `ReleaseEngine(config, runner)` — `plan(...)`, `publish_plan(project,
  artifacts)`, `execute(plan, dry_run, resume)`, `changed_projects()`.
- `Git(root, runner)` — `status_porcelain()`, `changed_paths(since)`,
  `tag_exists(tag)`, `tags(pattern)`.
- `Runner` protocol / `SubprocessRunner` — `run(command, cwd)` →
  `CommandResult(command, returncode, stdout, stderr)`; commands are argv
  tuples.
- `checksum_directory(directory, name)` — SHA-256 manifest for a directory
  of artifacts; `python -m tools.release_publish.artifacts`.
- `python -m tools.release_publish.upload` — safe `twine upload` wrapper
  (`--repository-url` forwarded).

## Provider boundaries

`RepositoryProvider` is a protocol. `GitHubProvider` is the bundled adapter and
uses GitHub's official `gh` CLI. A GitLab, Gitea, Bitbucket, Azure DevOps, or
internal adapter can implement the same boundary without modifying planning.
`PyPiProvider` similarly isolates Python build/upload behavior
(`python -m build`, `twine upload`, `--repository-url` for PEP 503 endpoints).

## Monorepo semantics

Dependencies are topologically ordered. A selected component can be released
without releasing unrelated siblings. Tags are namespaced, so each component
has its own history. Version files are explicit rather than guessed, which
makes plans auditable.

## Publishing an existing tag

`release-publish publish --project NAME --artifacts PATH [PATH ...] [--update] [--dry-run]` (files, or directories whose files) creates the
hosted release for a tag that was already pushed (`release --no-publish`), so
CI can build the platform artifacts between the two steps. It requires the tag
to exist locally and `DIR` to contain files, attaches every file in `DIR` in a
stable order, asks GitHub to generate the notes starting from the previous
release tag of the same project (versions are compared, prereleases included),
and marks versions with a prerelease segment (`1.0.0-rc.1`) as prereleases.

`release-publish show --project NAME` prints the project's path, version and
current tag as JSON for automation. `plan`/`release` accept `--skip-hooks` to
bypass configured pre/post hooks, and the planner stages and commits only the
project's `version_files` so unrelated or untracked files never enter a release
commit. A project may omit `version` and let the first version file supply it.

## Hooks

`pre_hooks` and `post_hooks` are arrays of argv arrays. They are deliberately
executed without a shell. Use them for tests, linting, SBOM generation, package
signing, provenance/attestation generation, documentation builds, deployment,
or organization-specific policy gates. `{python}` expands to the running
interpreter.

## Security

No token is accepted in configuration. Authentication belongs to provider
tooling (`gh auth`, Twine keyring/environment configuration, CI OIDC, etc.).
Commands are argv tuples and never pass through a shell. This avoids command
injection and keeps secrets out of release.toml. `Action.secret_indexes`
marks command arguments that must be masked when actions are rendered.

## Extending

Add adapters behind protocols rather than branching on provider names inside
the domain layer. Keep planning deterministic and move network/process effects
to execution boundaries. This allows the same release plan to target local
testing, CI, or a future remote execution service.

`publish --update` uploads to an already-existing release (`gh release upload
--clobber`) instead of creating one; `ReleaseEngine.release_exists(project)`
tells callers which to use.
