#!/bin/sh
# ensure-python.sh LIBDIR - guarantee a Python >= @@WANT_MAJOR@@.@@WANT_MINOR@@ interpreter for the
# bundled .pyz. Prefers an existing system interpreter; otherwise downloads a
# python-build-standalone tarball into LIBDIR/python. Never fails the install:
# a missing interpreter is reported again by `district-pyz --test` at run time.
# Rendered by tools.stationer.installer_support (@@WANT_MAJOR@@.@@WANT_MINOR@@, @@PYVER@@,
# @@TAG@@); the worker additionally expands {name} style placeholders, so
# this script must not use braces other than the ones below.
set -u
LIBDIR="$1"
WANT_MAJOR=@@WANT_MAJOR@@
WANT_MINOR=@@WANT_MINOR@@
PYVER="@@PYVER@@"
TAG="@@TAG@@"

satisfies() {
    command -v "$1" >/dev/null 2>&1 && \
    "$1" -c "import sys,os;os._exit(0 if sys.version_info[:2]>=($WANT_MAJOR,$WANT_MINOR) else 1)" \
        >/dev/null 2>&1
}

for candidate in python3 python; do
    if satisfies "$candidate"; then
        echo "python: $(command -v "$candidate") satisfies >=$WANT_MAJOR.$WANT_MINOR"
        exit 0
    fi
done

PRIVATE="$LIBDIR/python/bin/python3"
if [ -x "$PRIVATE" ] && satisfies "$PRIVATE"; then
    echo "python: $PRIVATE already present"
    exit 0
fi

CPU=$(uname -m)
case "$CPU" in
    x86_64|amd64) TRIPLE_CPU=x86_64 ;;
    arm64|aarch64) TRIPLE_CPU=aarch64 ;;
    *) echo "warning: unsupported CPU $CPU; skipping python bootstrap" >&2; exit 0 ;;
esac
KERNEL=$(uname -s)
case "$KERNEL" in
    Darwin) TRIPLE="$TRIPLE_CPU-apple-darwin" ;;
    Linux)
        if [ -f /etc/alpine-release ] || (ldd --version 2>&1 | grep -qi musl); then
            LIBC=musl
        else
            LIBC=gnu
        fi
        TRIPLE="$TRIPLE_CPU-unknown-linux-$LIBC"
        ;;
    *) echo "warning: unsupported OS $KERNEL; skipping python bootstrap" >&2; exit 0 ;;
esac

URL="https://github.com/astral-sh/python-build-standalone/releases/download/$TAG/cpython-$PYVER+$TAG-$TRIPLE-install_only_stripped.tar.gz"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "python: downloading $URL"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$TMP/python.tar.gz" "$URL" || { echo "warning: python download failed" >&2; exit 0; }
elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$TMP/python.tar.gz" "$URL" || { echo "warning: python download failed" >&2; exit 0; }
elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import urllib.request,sys;urllib.request.urlretrieve(sys.argv[1],sys.argv[2])" \
        "$URL" "$TMP/python.tar.gz" || { echo "warning: python download failed" >&2; exit 0; }
else
    echo "warning: no curl, wget or python3 available to fetch python" >&2
    exit 0
fi
mkdir -p "$LIBDIR/python"
tar -xzf "$TMP/python.tar.gz" -C "$LIBDIR/python" --strip-components=1 2>/dev/null || \
tar -xzf "$TMP/python.tar.gz" -C "$LIBDIR/python" || true
# install_only trees unpack to ./python; --strip-components may already level it.
if [ ! -x "$LIBDIR/python/bin/python3" ] && [ -x "$LIBDIR/python/python/bin/python3" ]; then
    mv "$LIBDIR/python/python"/* "$LIBDIR/python/"
    rmdir "$LIBDIR/python/python"
fi
if satisfies "$PRIVATE"; then
    echo "python: installed $PYVER into $LIBDIR/python"
else
    echo "warning: private python failed the version check" >&2
fi
exit 0
