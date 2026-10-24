# `charter/`

Public API and CLI for exposing a local TCP port through a managed charter: `ngrok`, `pinggy`, `tunnl`, `runlocal` or `direct` (no charter). The stationer's remote pull workers reuse this package when `--expose` selects a backend.

```console
$ python -m tools.charter expose --port 8765 --backend pinggy
https://abc.free.pinggy.net
```

```python
from tools.charter import open_charter

with open_charter(port=8765, backend="tunnl") as charter:
    print(charter.public_url)
```

`[tool.charter]` in `pyproject.toml` sets the defaults both layers honour:

```toml
[tool.charter]
backend = "ngrok"
# ngrok_authtoken = "…"       # or set NGROK_AUTHTOKEN
# ngrok_executable = "ngrok"  # name or path; auto-install when absent
# ssh_executable = "ssh"      # pinggy and tunnl
# expect_executable = "expect"  # pinggy
# npx_executable = "npx"      # runlocal
# url_timeout_seconds = 20.0  # log-discovered URL wait
# ngrok_timeout_seconds = 15.0
# stop_timeout_seconds = 5.0  # graceful stop grace period
# shortener = "https://shrtr.top/api/v1/shorten"
```

CLI overrides: `--backend`, `--authtoken`, `--short [ALIAS]`, `--json`,
`--pyproject PATH`. Other commands: `backends`, `settings`, `shorten URL`,
`install-ngrok`.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Public API for exposing a local port through a managed charter.
- [`__main__.py`](__main__.py) — Module entry point for ``python -m tools.charter``.
- [`backends.py`](backends.py) — Charter backends: ngrok, pinggy, tunnl, runlocal and direct.
- [`cli.py`](cli.py) — Command-line interface for the charter backends.
- [`errors.py`](errors.py) — Typed errors shared by every charter backend.
- [`http.py`](http.py) — Minimal HTTP(S) requests used by the charter backends.
- [`lifecycle.py`](lifecycle.py) — Charter lifetime: keep alive, expire after a time, stop on command.
- [`models.py`](models.py) — Typed values shared by the charter backends.
- [`settings.py`](settings.py) — Layered charter settings: defaults < ``pyproject.toml`` < CLI overrides.
- [`shorten.py`](shorten.py) — Anonymous URL shortening for exposed endpoints.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
