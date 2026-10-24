#!/bin/sh
# @@NAME@@-pyz - run the bundled .pyz with a guaranteed Python >= @@WANT_MAJOR@@.@@WANT_MINOR@@.
# Rendered by tools.stationer.installer_support (@@NAME@@, @@PYZ@@,
# @@LIBDIR@@, @@WANT_MAJOR@@, @@WANT_MINOR@@).
LIBDIR="@@LIBDIR@@"
PYZ="$LIBDIR/@@PYZ@@"
PRIVATE="$LIBDIR/python/bin/python3"

pick() {
    command -v "$1" >/dev/null 2>&1 && \
    "$1" -c "import sys,os;os._exit(0 if sys.version_info[:2]>=(@@WANT_MAJOR@@,@@WANT_MINOR@@) else 1)" \
        >/dev/null 2>&1
}

if [ -x "$PRIVATE" ] && pick "$PRIVATE"; then
    exec "$PRIVATE" "$PYZ" "$@"
fi
for candidate in python3 python; do
    if pick "$candidate"; then
        exec "$candidate" "$PYZ" "$@"
    fi
done
echo "@@NAME@@-pyz: no Python >=@@WANT_MAJOR@@.@@WANT_MINOR@@ found; reinstall or install python3" >&2
exit 127
