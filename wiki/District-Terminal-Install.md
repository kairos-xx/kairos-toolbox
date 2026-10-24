# Installing District Terminal

The installer is component-based: it owns exactly the artifacts it creates,
records them in a manifest, and removes only them on uninstall.

```bash
python tools/district/install.py              # wizard (TTY only)
python tools/district/install.py install --yes
python tools/district/install.py plan         # dry preview
python tools/district/install.py status
python tools/district/install.py uninstall    # + --purge removes state
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
| Launcher | The `district` command entry point |
| tmux | Managed session backend on Unix |
| Shell hooks | zsh / bash / PowerShell lifecycle integration |
| Agent routing | Provider-aware session routing |
| Provider hooks | Claude / Codex / Cursor / Devin integration endpoints |
| Web autostart | Per-user dashboard startup |
| URI handler | Native `district://` OS handler → `district --handle-uri` |

The URI handler registers `DistrictURI.app` (macOS), a `.desktop` entry
(Linux), or registry `URL Protocol` entries (Windows). The handler command
simply invokes the installed launcher — which already knows how to start
District Terminal in every install mode — rather than rebuilding import paths.

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
| `--force-alias` | Replace a District Terminal-owned conflicting alias |
| `--mode live|copy|pyz` | Deployment mode |
| `--bin-dir` / `--lib-dir` / `--home` | Layout overrides |
| `--yes`, `--force`, `--dry-run`, `--quiet` | Interactivity & safety |

## The manifest

Everything installed is recorded under `~/.district/` so
`uninstall` removes exactly what the installer owns — never user files it
didn't create. `--purge` additionally removes control-plane state.

## Updates

`district --update [VERSION]` pulls a release from GitHub Releases; live
checkouts are never rewritten. Policy lives in `~/.district/update.json`.
See [CLI reference](District-Terminal-CLI#updates) and [Releasing](Releasing)
for how those archives are produced.
