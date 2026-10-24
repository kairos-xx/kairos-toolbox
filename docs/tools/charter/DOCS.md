# Charter — Extended Documentation and API Reference

`tools.charter` exposes a local TCP port through a managed public endpoint.
It is a deliberately small, dependency-free package: five backends
(`direct`, `ngrok`, `pinggy`, `tunnl`, `runlocal`), one layered settings
system, one process lifecycle model, and one anonymous URL shortener —
usable identically from the `charter` CLI and from Python.

Two consumers depend on it in this repository:

- **District Terminal** — `district --web --expose BACKEND` (and
  `--qr` over a tunnel) publishes the web dashboard through
  `tools.district.exposure`, which loads `tools.charter` lazily so packaged
  builds without the package fail with a clear error rather than an
  `ImportError`.
- **Stationer** — `--remote TARGET --expose BACKEND` publishes the
  authenticated worker job server so a VM or remote machine can pull its
  build job over HTTPS (`tools.stationer.remote_exposure` facade).

## Contents

- [Architecture](#architecture)
- [Backend reference](#backend-reference)
- [`[tool.charter]` settings reference](#toolcharter-settings-reference)
- [CLI reference](#cli-reference)
- [Python API](#python-api)
- [Lifecycle, registry and expiry](#lifecycle-registry-and-expiry)
- [URL shortening](#url-shortening)
- [Consumer integration](#consumer-integration)
- [Security](#security)
- [Extending](#extending)

## Architecture

The package separates four concerns:

| Layer | Module | Responsibility |
| --- | --- | --- |
| Dispatch | `backends.py` | Resolve executables, spawn each backend, discover its public URL. |
| Settings | `settings.py` | Merge built-in defaults `<` `[tool.charter]` in `pyproject.toml` `<` explicit overrides into an immutable `Settings`. |
| Lifecycle | `lifecycle.py` | Scoped vs. keep-alive tunnels, the JSON registry, TTL expiry. |
| Values | `models.py` | The `Charter` handle (process + public URL) and shared constants. |

`http.py` (a single `http_request`) and `shorten.py` are leaf utilities;
`errors.py` defines the one exception type, `CharterError`, that every
backend and lifecycle failure is reported as.

A call to `open_charter(port, backend, settings)`:

1. Resolves settings (loader + overrides).
2. Refuses `keep_alive` on Windows (`os.kill(pid, 0)` terminates there —
  see [POSIX-only keep-alive](#posix-only-keep-alive)).
3. Rejects unknown backend names against `CharterConstants.backends`.
4. Returns `None` immediately for `direct` — there is nothing to expose,
  and callers treat `None` as "serve locally only".
5. Runs the backend's starter, which either spawns a logged subprocess
  (`pinggy`, `tunnl`, `runlocal`) or manages ngrok's own daemon.
6. Waits for the public HTTPS URL — by polling ngrok's local inspection
  API, or by scanning the backend's log for the first `https://…` URL
  whose host ends in the backend's known suffix — until
  `url_timeout_seconds` / `ngrok_timeout_seconds` elapses.
7. Applies the configured lifetime (`manage`): registers keep-alive
  tunnels and arms TTL expiry.
8. Returns a running `Charter` handle.

If the URL never appears, the spawned process is terminated before the
`CharterError` propagates — a failed `open_charter` never leaks a backend.

## Backend reference

| Backend | Account needed | Extra tools | Mechanism | URL host suffix |
| --- | --- | --- | --- | --- |
| `direct` | — | — | No-op; returns `None`. | — |
| `ngrok` | Free authtoken | `ngrok` (auto-installed) | `ngrok http PORT --log stdout`, URL polled from `http://127.0.0.1:4040/api/tunnels`. | `*.ngrok*` |
| `pinggy` | — | `expect`, `ssh` | `expect` script drives `ssh -R0:localhost:PORT free.pinggy.io` with a blank password. | `*.free.pinggy.net` |
| `tunnl` | — | `ssh` | `ssh -n -T -R 80:localhost:PORT proxy.tunnl.gg output=json`. | `*.tunnl.gg` |
| `runlocal` | — | `npx` | `npx --yes runlocal PORT`. | `*.runlocal.eu` |

### ngrok

`install_ngrok` locates the executable on `PATH` (honouring
`ngrok_executable`) or installs it with an official method:

- **macOS** — `brew install ngrok` (requires Homebrew).
- **Windows** — `winget install ngrok -s msstore` with license acceptance
  flags.
- **Linux** — downloads the official `bin.ngrok.com` tarball for
  `x86_64`/`aarch64` into `~/.cache/charter/ngrok` and marks it
  executable; other architectures fail with a clear error.

Authentication resolves `settings.ngrok_authtoken` first, then the
`NGROK_AUTHTOKEN` environment variable, then ngrok's own configuration
(`ngrok config add-authtoken …`). The free plan still requires a free
account; when no tunnel appears within `ngrok_timeout_seconds` the error
message says so explicitly.

The public URL is the first `https://` entry in the local inspection
API's `tunnels` list — never scraped from stdout.

### pinggy

Pinggy's free tier accepts reverse SSH with a blank password. The backend
spawns `expect` non-interactively:

```
spawn ssh -o StrictHostKeyChecking=accept-new -p 443 -R0:localhost:PORT free.pinggy.io
expect "password:"; send "\r"
expect -re {https://….free\.pinggy\.net}
```

`StrictHostKeyChecking=accept-new` means the first connection works
without an interactive host-key prompt while still pinning the key for
later runs.

### tunnl

A plain reverse-SSH port forward (`-R 80:localhost:PORT`) to
`proxy.tunnl.gg`; `output=json` makes the server print its assigned URL
in machine-readable form, which the log scanner picks up.

### runlocal

`npx --yes runlocal PORT` downloads and runs the upstream CLI on demand —
nothing is installed permanently, but Node.js/npm must already exist.

## `[tool.charter]` settings reference

Every key is optional; the table itself is optional. Unknown keys raise
`ValueError`, so typos fail loudly rather than silently applying
defaults. Values must have the right JSON type (`TypeError` otherwise).

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `backend` | string | `"direct"` | `direct`, `ngrok`, `pinggy`, `tunnl` or `runlocal`. |
| `json_output` | bool | `false` | CLI emits one JSON value instead of text. |
| `ngrok_authtoken` | string | — | Overrides `NGROK_AUTHTOKEN`; keep real tokens in the environment, not in files that get committed. |
| `ngrok_executable` | string | `"ngrok"` | Executable name or absolute path. |
| `ssh_executable` | string | `"ssh"` | OpenSSH client for `pinggy` and `tunnl`. |
| `expect_executable` | string | `"expect"` | `expect` interpreter for `pinggy`. |
| `npx_executable` | string | `"npx"` | `npx` launcher for `runlocal`. |
| `url_timeout_seconds` | number | `20.0` | Wait for a log-discovered URL. |
| `ngrok_timeout_seconds` | number | `15.0` | Wait for ngrok's API to report a tunnel. |
| `stop_timeout_seconds` | number | `5.0` | Grace period between `SIGTERM` and `SIGKILL`. |
| `shortener` | string | `https://shrtr.top/api/v1/shorten` | Anonymous URL-shortening endpoint. |
| `keep_alive` | bool | `false` | Tunnels outlive the Python process (POSIX only). |
| `ttl_seconds` | number | — | Auto-stop each tunnel this many seconds after start; must be positive. |
| `registry_path` | string | `~/.charter/tunnels.json` | JSON file tracking keep-alive tunnels. |

### Precedence

```
built-in defaults  <  [tool.charter] in the nearest pyproject.toml  <  explicit overrides
```

`SettingsLoader.discover_pyproject` walks upward from the current
directory (or an explicit `--pyproject PATH`), so the same settings apply
no matter where inside a checkout the CLI runs. CLI flags and
`SettingsLoader.load(overrides=…)` win last; `None` override values are
ignored so callers can pass sparse dictionaries.

## CLI reference

```bash
python -m tools.charter [--pyproject PATH] [--json | --no-json] <command>
```

`main` returns `0` on success and `1` on any handled failure
(`CharterError`, `OSError`, `ValueError`, `TypeError`, `KeyError`); parse
errors exit with argparse's status `2`.

### `expose --port PORT [--backend NAME] [--authtoken TOKEN] [--short [ALIAS]] [--background] [--ttl SECONDS]`

Start a backend for `PORT` and print the public URL. Without
`--background` the command blocks until the backend exits or Ctrl-C is
pressed, then stops it (`charter.close()`). With `--background` it prints
the URL and returns immediately while the tunnel keeps running (implies
`keep_alive`; end it later with `stop`). `--short` additionally prints a
shortened URL, using `ALIAS` when given.

Text output is the public URL (or `direct: nothing to expose on port N`);
`--json` emits `{"backend", "port", "public_url", "short_url"}`.

### `list`

Print the keep-alive tunnels whose processes are still running:
`id`, `backend`, `port`, `url`, `expires_at`. Dead records are pruned
from the registry automatically.

### `stop [ID ...] [--all]`

Terminate registered tunnels by id (`ID` is the backend's PID as text),
waiting `stop_timeout_seconds` before escalating to `SIGKILL`. Fails with
an error when neither an id nor `--all` is given, or when an id is
unknown.

### `expire --pid PID --seconds SECONDS`

Internal watchdog: sleep `SECONDS`, then end the tunnel process `PID`
(and drop its registry record). `schedule_expiry` spawns it detached so
a keep-alive tunnel with a TTL expires even after the parent exits.

### `backends`, `settings`, `shorten URL [--alias A]`, `install-ngrok`

`backends` lists the accepted names; `settings` prints every resolved
field and its value (verify configuration without starting anything);
`shorten` shortens one URL; `install-ngrok` resolves or installs the
ngrok executable and prints its path — a dry way to validate the
auto-install path before depending on it.

## Python API

Everything below is re-exported from `tools.charter`.

```python
from tools.charter import open_charter

# Scoped: stops when the block ends.
with open_charter(port=8765, backend="tunnl") as charter:
    print(charter.public_url)
```

| API | Signature | Notes |
| --- | --- | --- |
| `open_charter` | `(port, backend=None, settings=None) -> Charter \| None` | Main entry point; `None` for `direct`. |
| `start_ngrok` | `(port, settings=None) -> Charter` | ngrok-specific starter (install + wait). |
| `install_ngrok` | `(settings=None) -> Path` | Find or install the executable. |
| `shorten_url` | `(url, alias=None, settings=None) -> str` | Anonymous shortening; requires an `https://` result. |
| `http_request` | `(url, timeout, data=None, headers=None) -> bytes` | GET, or POST when `data` is given; refuses non-HTTP(S) schemes. |
| `Settings` | frozen dataclass | One field per `[tool.charter]` key. |
| `SettingsLoader` | `load(pyproject=None, overrides=None)`, `discover_pyproject(start)` | Layered resolution. |
| `Charter` | `process`, `public_url`, `log_path`, `keep_alive`; `close()`, `stop()`, context-manager | `close` honours `keep_alive`; `stop` always terminates. |
| `CharterRegistry` | `add`, `records`, `stop(identifier)`, `stop_pid(pid)` | Registry of keep-alive tunnels. |
| `CharterRecord` | frozen dataclass | `identifier`, `pid`, `backend`, `port`, `public_url`, `started_at`, `expires_at`, `log_path`. |
| `CharterConstants` | class | `backends`, `ngrok_api`, `ngrok_download`, `ngrok_architectures`, `shortener`, default timeouts. |
| `CharterError` | `RuntimeError` subclass | Every charter failure. |

### Scoped vs. keep-alive

`Charter.close()` stops the tunnel unless `keep_alive` was resolved true —
so `with` blocks and consumer cleanup paths are always safe to call.
`Charter.stop()` terminates unconditionally: `SIGTERM`, wait
`stop_timeout_seconds`, then `SIGKILL`, and the temporary log is removed.

Keep-alive mode spawns the backend with `start_new_session=True`, records
it in the registry, and requires POSIX (see below). It exists for
workflows like `stationer --remote … --expose ngrok`, where the
tunnel must outlive the command that created it.

## Lifecycle, registry and expiry

The registry is a JSON array at `registry_path`
(default `~/.charter/tunnels.json`), replaced atomically on every write.
`CharterRegistry.records()` prunes dead PIDs on read, so `list` never
shows ghosts. Records are keyed by the backend PID.

`ttl_seconds` expiry differs by lifetime kind:

- **Scoped** — a daemon `threading.Timer` calls `stop` after the delay.
- **Keep-alive** — the parent may exit before the TTL, so a detached
  `python -m tools.charter expire --pid … --seconds …` watchdog does it
  instead.

### POSIX-only keep-alive

`require_detach_support` raises `CharterError` on Windows before any
backend starts: `os.kill(pid, 0)` — the liveness probe the registry and
the terminator rely on — kills the process on Windows. Scoped tunnels
work everywhere.

## URL shortening

`shorten_url(url, alias)` posts `{"url": …, "alias": …}` as JSON to the
configured `shortener` endpoint and requires an `https://` `short_url`
in the response. It is anonymous — no API key. `expose --short` and
`open_exposure(shorten=True)` route through it; the endpoint can be
replaced with any compatible service via the `shortener` setting.

## Consumer integration

### District Terminal

`district --web --expose pinggy` resolves `[tool.district.charter]`
(backend, `ngrok_authtoken`, `alias`, `shorten`) — product-level choices —
and merges them over `[tool.charter]` (mechanics). `tools.charter` is
imported lazily so an installed `district` build, which does not bundle
the tools, reports `ExposureError` instead of crashing. `direct` returns
`None` and the dashboard simply stays local.

### Stationer

`--remote TARGET --expose BACKEND` serves the worker job over a public
URL so a VM on any network can pull it; `--short ALIAS` shortens the
advertised URL. `remote_exposure` is a facade that re-exports the
`tools.charter` API under the builder's older names (`start_charter`,
`install_ngrok`, `start_ngrok`, `shorten_url`, `ExposureError`,
`ExposureConstants`) — keep it rather than importing `tools.charter` from
new builder code when backward-compatible names matter.

## Security

- **No shell interpolation** — every backend is spawned as an argv list
  (`Popen(args=[…])`), never through a shell; ports and paths cannot
  inject commands.
- **URL discovery is host-suffixed** — the log scanner only accepts
  `https://` URLs whose host ends in the backend's known suffix, so a
  backend cannot redirect callers to an arbitrary domain.
- **Scheme allow-list** — `http_request` refuses non-HTTP(S) URLs; the
  shortener's answer must itself be `https://`.
- **Secrets stay out of files** — `ngrok_authtoken` belongs in
  `NGROK_AUTHTOKEN` or ngrok's own config; the `[tool.charter]` key exists
  for CI injection but committing a real token exposes it in git.
- **Temporary logs are cleaned** — `stop`/`close` remove the URL log; a
  backend that never produced a URL is terminated before the error
  surfaces, so nothing is left running after a failure.
- **Exposure is opt-in** — the default backend is `direct`; nothing opens
  a public endpoint unless configured or asked.

## Extending

A new backend needs four small additions in `backends.py` plus its name
in `CharterConstants.backends`:

1. A `_start_<name>(port, settings) -> Charter` starter — reuse
   `_spawn_logged(command, suffix, settings)` when the backend prints its
   URL to stdout/stderr, or a custom poller like ngrok's when it exposes
   a local API.
2. Its executable(s) resolved through `_require` (or `Settings` fields
   when the path must be configurable — then also add the key to
   `Settings`, `SettingsLoader._FIELDS` and `[tool.charter]` docs).
3. A host suffix for `_wait_for_url`, so only the real public URL is
   accepted.
4. A `starters` entry in `open_charter` — and, if the CLI should select
   it, nothing more: `expose --backend` choices come from
   `CharterConstants.backends` automatically.

Keep the invariants: argv-only spawning, suffixed HTTPS URL discovery,
termination on failure, and registration through `manage` for
keep-alive/TTL support.
