# `release_builder/`

Cross-platform Python release builder.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Cross-platform Python release builder.
- [`__main__.py`](__main__.py) — Run the cross-platform release builder.
- [`build_report.py`](build_report.py) — Maintain the merged artifact manifest and the generated ``BUILD.md``.
- [`publisher.py`](publisher.py) — Publish built artifacts to a hosted release through ``release_publish``.
- [`pypi_builder.py`](pypi_builder.py) — Build PyPI distributions (sdist + wheel) for a project and its siblings.
- [`pyz_builder.py`](pyz_builder.py) — Build a self-contained, platform-specific ``.pyz`` archive.
- [`release-builder.example.toml`](release-builder.example.toml) — Provide release builder.example toml content.
- [`release_builder.py`](release_builder.py) — Build native Python installers for macOS, Linux, and Windows ARM64.
- [`wiki.py`](wiki.py) — Synchronize a local ``wiki/`` directory with the repository's GitHub Wiki.
- [`windows-guest-setup.ps1`](windows-guest-setup.ps1) — Provide windows guest setup ps1 content.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
