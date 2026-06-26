"""Shared config plumbing for the block-device image-builder apps.

The builder is split into standalone apps — `build` (rootless),
`capture`, and `write` — that each own their CLI (argparse) but share how
settings are sourced. This module is that shared layer:

- load the app's `bd-config.toml` tables (e.g. `[image]` + `[build]`)
  over a built-in default map, erroring on an unknown key,
- merge file < `BD_<KEY>` env < CLI flag into a `{key: (value, source)}`
  plan so `--dry-run` can show where each value won,
- generate the per-key CLI flags and print the resolved settings block.

Settings map 1:1 to CLI flags — identical names (`compress` <->
`--compress`). Precedence, lowest to highest: the config tables, then an
`BD_<KEY>` env var, then the matching CLI flag. The config file defaults
to `bd-config.toml` in the current working directory; each app's
`--config` overrides it.
"""

import argparse
import os
import tomllib
from pathlib import Path

# Default config: bd-config.toml in the current working directory. An
# installed `bd-*` tool reads the config of the project it is run in;
# `--config` overrides it. A missing file falls back to built-in defaults.
DEFAULT_CONFIG = Path("bd-config.toml")

# Canonical schema: every valid key in each bd-config.toml table. Used to
# reject typos (an unknown key in a table) while still letting an app read
# only the subset of a shared table it needs — e.g. capture reads [image]
# but not its `output`, yet `output` must not look like a typo there.
TABLE_KEYS = {
    "image": {"images_dir", "mother", "output"},
    "build": {"shrink", "compress"},
    "capture": {"source"},
    "write": {"target", "target_byid", "expand", "allow_yes"},
}

# BD_* env values that count as true / false (case-insensitive). Anything
# else is a hard error so a typo never silently flips a toggle.
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_bool(raw: str, source: str) -> bool:
    """Coerce a BD_* env string to bool, erroring on anything unknown.

    - `source` names the variable for the error message,
    - accepts the _TRUE / _FALSE token sets, case-insensitively.
    """
    token = raw.strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise SystemExit(
        f"imgcfg: {source}={raw!r} is not a boolean "
        f"(use one of {sorted(_TRUE)} / {sorted(_FALSE)})"
    )


def load_config(path: Path, sections: tuple, defaults: dict) -> dict:
    """Return `defaults` overlaid by the named `sections` of the config.

    - `sections` are the `bd-config.toml` tables the app reads (e.g.
      `("image", "build")`); later sections win on a key overlap,
    - a missing file is fine — fall back to `defaults` entirely so
      `--dry-run` works on a bare checkout,
    - a key not in a read section's canonical schema (`TABLE_KEYS`) is an
      error (a typo); a valid key the app simply doesn't resolve — e.g.
      capture reading [image] but not its `output` — is kept out of the
      plan, not flagged.
    """
    merged = dict(defaults)
    if not path.exists():
        return merged
    doc = tomllib.loads(path.read_text())
    for section in sections:
        table = doc.get(section, {})
        unknown = set(table) - TABLE_KEYS.get(section, set())
        if unknown:
            raise SystemExit(
                f"imgcfg: {path} [{section}] has unknown key(s): "
                f"{', '.join(sorted(unknown))}"
            )
        merged.update({k: v for k, v in table.items() if k in defaults})
    return merged


def resolve(args: argparse.Namespace, file_cfg: dict,
            str_keys: tuple, bool_keys: tuple) -> dict:
    """Merge file < BD_* env < CLI into a {key: (value, source)} plan.

    - `source` records where the winning value came from ("config",
      "env", or "flag") so `--dry-run` can show the override chain,
    - CLI flags default to None (unset) so an absent flag falls through
      to env, then to the file default.
    """
    resolved: dict = {}
    for key in (*str_keys, *bool_keys):
        value = file_cfg[key]
        source = "config"

        env_name = f"BD_{key.upper()}"
        if env_name in os.environ:
            raw = os.environ[env_name]
            value = parse_bool(raw, env_name) if key in bool_keys else raw
            source = "env"

        cli = getattr(args, key)
        if cli is not None:
            value = cli
            source = "flag"

        resolved[key] = (value, source)
    return resolved


def add_arguments(parser: argparse.ArgumentParser,
                  str_keys: tuple, bool_keys: tuple) -> None:
    """Add one config-override flag per key to `parser`.

    - string keys take a value (`--key`); bool keys use
      BooleanOptionalAction so each gets a `--key` / `--no-key` pair,
    - every flag defaults to None (unset) so `resolve` can tell an absent
      flag from an explicit one and apply the precedence chain.
    """
    for key in str_keys:
        parser.add_argument(
            f"--{key.replace('_', '-')}", default=None,
            help=f"override config {key}",
        )
    for key in bool_keys:
        parser.add_argument(
            f"--{key.replace('_', '-')}", default=None,
            action=argparse.BooleanOptionalAction,
            help=f"override config {key}",
        )


def img_path(plan: dict, key: str) -> Path:
    """Resolve a `mother` / `output` plan value against `images_dir`.

    A bare filename (no directory part) sits under `images_dir`; a value
    that already carries a path — relative or absolute — is used as-is.
    Shared by the apps that move these artifacts (build, capture, write).
    """
    p = Path(plan[key][0])
    if p.parent == Path("."):
        return Path(plan["images_dir"][0]) / p
    return p


def print_settings(config_path: Path, plan: dict,
                   str_keys: tuple, bool_keys: tuple, prog: str) -> None:
    """Print the resolved settings block: each value and where it won.

    - the header notes whether the config file was present,
    - string keys show `repr`, bool keys show `on` / `off`; the `[source]`
      column is `config` / `env` / `flag`.
    """
    exists = "present" if config_path.exists() else "missing, using built-in defaults"
    print(f"{prog} plan (config: {config_path} — {exists})")
    print("")
    print("settings:")
    for key in (*str_keys, *bool_keys):
        value, source = plan[key]
        shown = repr(value) if key in str_keys else ("on" if value else "off")
        print(f"  {key:<22} {shown:<26} [{source}]")
