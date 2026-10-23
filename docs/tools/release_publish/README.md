# release-publish

`release-publish` is a provider-agnostic release control plane for a single
repository or a monorepo containing independently versioned projects.

It deliberately separates **planning** from **execution**. A release is first
compiled into an immutable plan; the same plan can be inspected, rendered,
run as a dry-run, or executed. GitHub and PyPI are adapters, not assumptions.

## Capabilities

- create/update GitHub repositories through the official `gh` CLI;
- independent root/component versions and namespaced tags;
- detect components changed since their own last tag;
- dependency-aware topological release ordering;
- bump versions (`major`, `minor`, `patch`, or explicit versions);
- update version declarations in arbitrary files;
- build Python sdists/wheels using `python -m build`;
- publish to PyPI or any PEP 503-compatible upload endpoint via `twine`;
- create/update GitHub Releases and upload arbitrary artifacts;
- SHA-256 checksum manifests;
- configurable pre/post hooks for tests, SBOM, signing, provenance, etc.;
- deterministic plans, dry runs, command tracing, journals and resume support;
- no runtime Python dependencies.

## Minimal configuration

```toml
[repository]
provider = "github"
owner = "acme"
name = "platform"
default_branch = "main"
visibility = "private"

[release]
tag_template = "{project}/v{version}"
root_tag_template = "v{version}"

[projects.api]
path = "services/api"
version = "1.4.0"
version_files = ["services/api/pyproject.toml"]
python_distribution = true

[projects.sdk]
path = "packages/sdk"
version = "2.1.0"
version_files = ["packages/sdk/pyproject.toml"]
python_distribution = true
depends_on = ["api"]
```

## CLI

```bash
release-publish init
release-publish status
release-publish plan --project api --bump minor
release-publish release --project api --bump minor --dry-run
release-publish release --project api --bump minor
release-publish release --changed --bump patch
release-publish repo-sync --dry-run
```

See [`DOCS.md`](DOCS.md) for the architecture and full configuration reference.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`DOCS.md`](DOCS.md) — `tools.release_publish` is a provider-agnostic release control plane for a single repository or a monorepo containing independently versioned projects. It deliberately separates **planning** from **execution**: a release is first compiled into an immutable plan; the same plan can be inspected, rendered, run as a dry-run, executed, journaled, and resumed.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
