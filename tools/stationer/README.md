# `stationer/`

Cross-platform release builder and publishing control plane.

```console
$ python -m tools.stationer --project tools/district \
    --manifest tools/district/templates/stationer.toml \
    --output dist --format pyz          # build artifacts
$ python -m tools.stationer --config release.toml plan \
    --project district --bump minor     # release control plane
$ python -m tools.stationer               # pipeline from [tool.stationer.builder]
```

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Cross-platform release builder and publishing control plane.
- [`__main__.py`](__main__.py) — Build and publish releases: ``python -m tools.stationer``.
- [`artifacts.py`](artifacts.py) — Artifact post-processing commands.
- [`build_report.py`](build_report.py) — Maintain the merged artifact manifest and the generated ``BUILD.md``.
- [`builder.py`](builder.py) — Build native Python installers for macOS, Linux, and Windows ARM64.
- [`cli.py`](cli.py) — Command-line interface.
- [`config.py`](config.py) — TOML configuration loading and validation.
- [`conjure.py`](conjure.py) — Job types and result verification for remote pull-worker builds.
- [`engine.py`](engine.py) — Release execution engine with dry-run and journaling.
- [`git.py`](git.py) — Git repository adapter.
- [`installer_support.py`](installer_support.py) — Shared installer-payload helpers for every native builder.
- [`models.py`](models.py) — Immutable domain models.
- [`planner.py`](planner.py) — Release planning and dependency ordering.
- [`progress.py`](progress.py) — Track and display build progress reported by VM and container guests.
- [`providers.py`](providers.py) — Repository and package-registry adapters.
- [`publisher.py`](publisher.py) — Publish built artifacts to a hosted release through ``stationer``.
- [`pypi_builder.py`](pypi_builder.py) — Build PyPI distributions (sdist + wheel) for a project and its siblings.
- [`pyz_builder.py`](pyz_builder.py) — Build a self-contained, platform-specific ``.pyz`` archive.
- [`remote_builder.py`](remote_builder.py) — Build on a remote native worker that pulls the job from this host.
- [`remote_exposure.py`](remote_exposure.py) — Public exposure and URL shortening for the build host.
- [`remote_server.py`](remote_server.py) — Authenticated one-job HTTP control plane for remote build workers.
- [`remote_workers.py`](remote_workers.py) — Render the Windows and POSIX remote build workers.
- [`runner.py`](runner.py) — Run release commands through ``conjure`` host sessions.
- [`sessions.py`](sessions.py) — Open the ``conjure`` runners the platform builders execute in.
- [`upload.py`](upload.py) — Safe package-registry upload helper.
- [`versioning.py`](versioning.py) — Strict semantic-version operations.
- [`wiki.py`](wiki.py) — Synchronize a local ``wiki/`` directory with the repository's GitHub Wiki.

### Subdirectories

- [`templates/`](templates/README.md) — Provide stationer.example toml content.

<!-- exactus:inventory:end -->
