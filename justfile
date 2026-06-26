# bdimager recipes — run `just --list` to see them.
#
# The image-editing recipes (build and the *-test smokes) are rootless:
# they edit the filesystems inside the .img with debugfs / mtools, no
# sudo. Only capture / write to a real block device need root.

# Show available recipes.
_default:
    @just --list

# Run the test suite (e.g. `just test -k build`).
test *args:
    uv run pytest {{args}}

# Build a deployable image from a mother, rootless (--add, --dry-run, ...).
build *args:
    uv run bd-build {{args}}

# Capture a source block device into the mother (needs sudo; --list-devices).
capture *args:
    uv run bd-capture {{args}}

# Write a deployable image to a target device (DESTRUCTIVE, needs sudo).
write *args:
    uv run bd-write {{args}}

# Build/verify a synthetic test image: `just mk-test-image build out.img`.
mk-test-image *args:
    uv run python -m bdimager.mk_test_image {{args}}

# Rootless self-test of build: synthesize a mother, build+shrink+xz, verify.
image-test:
    #!/usr/bin/env bash
    set -euo pipefail
    m=images/_test-mother.img; d=images/_test-deploy.img
    trap 'rm -f "$m" "$d" "$d".xz' EXIT
    uv run python -m bdimager.mk_test_image build "$m"
    uv run bd-build --mother "$m" --output "$d" --shrink --compress
    uv run python -m bdimager.mk_test_image verify "$d"

# Rootless self-test of capture: synthetic card -> mother -> build -> verify.
capture-test:
    #!/usr/bin/env bash
    set -euo pipefail
    card=images/_test-card.img; m=images/_test-mother.img; d=images/_test-deploy.img
    trap 'rm -f "$card" "$m" "$d" "$d".xz' EXIT
    uv run python -m bdimager.mk_test_image build "$card"
    uv run bd-capture --test-mode --source "$card" --mother "$m"
    uv run bd-build --mother "$m" --output "$d" --shrink --compress
    uv run python -m bdimager.mk_test_image verify "$d"

# Rootless self-test of write: write a deploy into a larger card, expand, verify.
write-test:
    #!/usr/bin/env bash
    set -euo pipefail
    m=images/_test-mother.img; d=images/_test-deploy.img; card=images/_test-card.img
    trap 'rm -f "$m" "$d" "$d".xz "$card"' EXIT
    uv run python -m bdimager.mk_test_image build "$m"
    uv run bd-build --mother "$m" --output "$d" --shrink
    truncate -s 160M "$card"
    uv run bd-write --test-mode --output "$d" --target "$card"
    uv run python -m bdimager.mk_test_image verify-expanded "$card"
