# TODO — state at end of session (2026-10-24)

Rule for everything below: **no lint suppressions, no skipped tests.**

## 1. QR code for the phone console (`district --web --qr`) — done, needs a real phone scan

* The dependency-free encoder is merged into `tools/district/qr.py`
  (`segno` removed from every `pyproject.toml` and from `SelfTest.dependencies`).
  Its table/alignment/glyph bugs are fixed and every EC level x version 1-10 x
  mask 0-7 decodes with OpenCV (`test_every_supported_matrix_decodes_with_opencv`).
* **You still have to scan it with a real iPhone** (`district --web --qr`) to confirm
  the LAN handshake -> charter redirect -> authenticated terminal flow end to end.
  This is the only remaining manual step.
* Docs are now in place: `tools/district/README.md` (quick start + a
  "Phone console and device pairing" section), `wiki/District-Terminal-CLI.md`
  (web-dashboard block + pairing paragraph) and
  `docs/tools/district/DOCS.md` (`--qr` CLI entry, a "Pairing" subsection
  under Web dashboard, `pairing`/`qr` API-reference sections, and the
  `web-auth.key` state-file row). `docs/tools/conjure/DOCS.md` documents
  `qemu_snapshot` (disk/QMP layers, `QemuSnapshotCli`, the
  `snapshot_image`/`baseline_snapshot` settings).
* `/favicon.ico` now returns 204 instead of 404 (the `_favicon` handler existed
  but was never registered; `test_web_assets` expects the empty answer).

## 2. Windows VM tests (no skips) — done

Both run for real against the UTM guest `Windows 2` (`utm:Windows 2` in
`[tool.stationer.tests]`); no SSH is needed any more.

* `test_windows_pipeline_inside_an_existing_vm` — pull-worker through the host
  address on the guest network (`UtmRunner.host_address()`), passes.
* `test_windows_pipeline_via_qemu_worker` — `WindowsQemuBuilder` driven through
  the UTM guest agent (`UtmRunner`), passes.
* `UtmRunner.run` is now synchronous (wrapper script + status file), which also
  fixed the 0-byte downloads (`utmctl exec` is async and `file pull` of a
  missing file only reports on stderr).

## 3. Exactus — done

Re-run with the project venv:
`export EXACTUS_PROJECT_PYTHON="$PWD/.venv/bin/python" VIRTUAL_ENV="$PWD/.venv" PATH="$PWD/.venv/bin:$PATH"`
then `exactus --pipeline all-fast` — all six tools pass: lints, coverage_tests,
docs, kwargs_typehints, readme, performance. (Single tools run as
`exactus --lints`, `exactus --docs`, etc.; `-t performance` alone leaves the
report web server running, use the pipeline instead.)
The kwargs auto-fix was **not** applied to `POINTER(cls=...)` in `conpty.py`
(false positive) — verified untouched.

## 4. Unrelated failures (other people's WIP, left untouched)

Release workflow tests, Bowman version, `test_remote_server` helper rename.
