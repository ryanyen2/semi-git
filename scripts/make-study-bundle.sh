#!/usr/bin/env bash
# Build a self-contained bundle for one remote participant.
#
#   scripts/make-study-bundle.sh <participant> <git|sgt> <coursecraft|confplan>
#
# Produces ~/study/bundles/<participant>.tgz holding the project copy, the
# handouts, a wheel of the sgt build we are testing, and an install script the
# participant runs once. Send them the file.
#
# Everything slow or secret is done here, on our machine, not theirs. The history
# view is already refreshed, so their first command is fast, and no API key goes
# in the bundle.
set -euo pipefail

SGT_SOURCE="${SGT_SOURCE:-$HOME/repos/semi-git}"
OUT="${OUT:-$HOME/study/bundles}"

if [ $# -ne 3 ]; then
    echo "usage: $0 <participant> <git|sgt> <coursecraft|confplan>" >&2
    exit 2
fi
participant="$1"; condition="$2"; project="$3"

"$SGT_SOURCE/scripts/setup-study-session.sh" "$participant" "$condition" "$project"
workspace="$HOME/study/$participant"

if [ "$condition" = sgt ]; then
    echo "Building the sgt wheel."
    (cd "$SGT_SOURCE" && uv build --wheel -o "$workspace/install" -q)
fi
mkdir -p "$workspace/install"

cat > "$workspace/install/setup.sh" <<'PARTICIPANT'
#!/usr/bin/env bash
# Run this once, from inside the folder you unpacked. It takes a few minutes.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv, which manages Python for this session."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Your system Python does not matter. uv fetches its own.
echo "Fetching Python 3.12."
uv python install 3.12

echo "Setting up the project."
cd "$here/work"
rm -rf .venv
uv venv -q --clear -p 3.12
uv pip install -q -p .venv/bin/python pytest

wheel=$(ls "$here/install"/*.whl 2>/dev/null | head -1 || true)
if [ -n "$wheel" ]; then
    echo "Installing the version control tool."
    uv venv -q --clear -p 3.12 "$here/toolenv"
    uv pip install -q -p "$here/toolenv/bin/python" "$wheel"
    mkdir -p "$here/bin"
    printf '#!/usr/bin/env bash\nexec "%s/toolenv/bin/sgt" "$@"\n' "$here" > "$here/bin/sgt"
    chmod +x "$here/bin/sgt"
fi

echo "Checking everything works."
.venv/bin/python -m pytest -q | tail -1
echo
echo "Done. Tell your facilitator you are ready."
PARTICIPANT
chmod +x "$workspace/install/setup.sh"

# The .env holds our API key. It must never travel in a bundle.
rm -f "$workspace/work/.env"

# Leave out anything built for this machine. A virtualenv bakes in absolute
# paths, so shipping ours would break their install rather than save them time.
mkdir -p "$OUT"
tar czf "$OUT/$participant.tgz" -C "$HOME/study" \
    --exclude="$participant/work/.venv" \
    --exclude="$participant/toolenv" \
    --exclude="$participant/bin" \
    --exclude="__pycache__" \
    --exclude=".DS_Store" \
    --exclude=".pytest_cache" \
    "$participant"
echo
echo "Bundle: $OUT/$participant.tgz  ($(du -h "$OUT/$participant.tgz" | cut -f1))"
echo "Send it, then have them unpack it and run install/setup.sh."
if [ "$condition" = sgt ]; then
    echo "Send the API key separately, at the start of the session. See remote-setup.md."
fi
