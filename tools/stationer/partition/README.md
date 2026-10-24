# `partition/`

Content-addressed partition engine for stationer.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Content-addressed partition engine for stationer.
- [`__main__.py`](__main__.py) — Run the partition CLI as ``python -m stationer.partition``.
- [`checksums.py`](checksums.py) — SHA-256 checksum manifest read/write helpers.
- [`cli.py`](cli.py) — Command-line surface for the content-addressed partition store.
- [`fetch.py`](fetch.py) — Bounded, retrying HTTPS fetch to atomic destinations.
- [`json_types.py`](json_types.py) — Shared JSON value types for the partition engine and callers.
- [`launcher.py`](launcher.py) — Offline dependency-aware runtime for partitioned filesystem tools.
- [`model.py`](model.py) — Generic tool and component model for partitioned builds.
- [`store.py`](store.py) — Deterministic, streaming content-addressed filesystem bundles.
- [`target.py`](target.py) — Build-target identity for partitioned releases.

### Subdirectories

- [`zstd_portable/`](zstd_portable/README.md) — Portable Zstandard CLI wrapper with lazy zipped-binary extraction.

<!-- exactus:inventory:end -->
