# `release_builder/`

Tests for the cross-platform release builder.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Tests for the cross-platform release builder.
- [`helpers.py`](helpers.py) — Assertion helpers that avoid ``assert`` in test sources.
- [`test_build_report.py`](test_build_report.py) — Tests for ``build/BUILD.md`` and the merged ``artifacts.json``.
- [`test_configured.py`](test_configured.py) — Tests for ``\[tool.release_builder\]``-driven bare invocations.
- [`test_helpers.py`](test_helpers.py) — Tests for the assertion helpers' failure paths.
- [`test_local_dependencies.py`](test_local_dependencies.py) — Tests for installing local sibling projects before the main project.
- [`test_publisher.py`](test_publisher.py) — Tests for publishing the built artifacts to a GitHub release.
- [`test_pypi_builder.py`](test_pypi_builder.py) — Tests for the PyPI distribution builder.
- [`test_pyz_builder.py`](test_pyz_builder.py) — Tests for the self-contained ``.pyz`` output format.
- [`test_real_pipelines.py`](test_real_pipelines.py) — Real-environment pipeline tests.
- [`test_release_builder.py`](test_release_builder.py) — Tests for release-builder configuration and deterministic helpers.
- [`test_wiki.py`](test_wiki.py) — Tests for the GitHub Wiki synchronizer.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
