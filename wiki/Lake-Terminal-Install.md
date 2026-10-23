# Installing Lake Terminal

The installer is component-based: it owns exactly the artifacts it creates,
records them in a manifest, and removes only them on uninstall.

```bash
python tools/lake_terminal/install.py              # wizard (TTY only)
python tools/lake_terminal/install.py install --yes
python tools/lake_terminal/install.py plan         # dry preview
python tools/lake_terminal/install.py status
python tools/lake_terminal/install.py uninstall    # + --purge removes state
```

## The wizard

Runs only on a TTY; skipped by `--yes` and `--dry-run`.

| Key | Action |
| --- | --- |
| `↑`/`↓` or `j`/`k` | Move |
| `space` | Toggle component |
| `a` | Edit command alias |
| `m` | Toggle live/copy mode |
| `enter` | Install |
| `q` | Cancel |

## Components

| Component | What it installs |
| --- | --- |
| Launcher | The `lake` command entry point |
| tmux | Managed session backend on Unix |
| Shell hooks | zsh / bash / PowerShell lifecycle integration |
| Agent routing | Provider-aware session routing |
| Provider hooks | Claude / Codex / Cursor / Devin integration endpoints |
| Web autostart | Per-user dashboard startup |
| URI handler | Native `lake-terminal://` OS handler → `lake --handle-uri` |

The URI handler registers `LakeTerminalURI.app` (macOS), a `.desktop` entry
(Linux), or registry `URL Protocol` entries (Windows). The handler command
simply invokes the installed launcher — which already knows how to start
Lake Terminal in every install mode — rather than rebuilding import paths.

## Deployment modes

| Mode | Behavior |
| --- | --- |
| `live` | Run in place from this checkout |
| `copy` | Copy the project under `--lib-dir` |
| `pyz` | Self-contained archive + launcher script |

## Options

| Option | Effect |
| --- | --- |
| `--preset minimal|default|full` | Component preset |
| `--with` / `--without` | Fine-grained component selection |
| `--alias NAME` | Install an additional/renamed command |
| `--force-alias` | Replace a Lake Terminal-owned conflicting alias |
| `--mode live|copy|pyz` | Deployment mode |
| `--bin-dir` / `--lib-dir` / `--home` | Layout overrides |
| `--yes`, `--force`, `--dry-run`, `--quiet` | Interactivity & safety |

## The manifest

Everything installed is recorded under `~/.lake_terminal/` so
`uninstall` removes exactly what the installer owns — never user files it
didn't create. `--purge` additionally removes control-plane state.

## Updates

`lake --update [VERSION]` pulls a release from GitHub Releases; live
checkouts are never rewritten. Policy lives in `~/.lake_terminal/update.json`.
See [CLI reference](Lake-Terminal-CLI#updates) and [Releasing](Releasing)
for how those archives are produced.
