"""layout_engine — YAML layout loading, validation, and management."""

import os

import yaml

LAYOUTS_DIR = os.path.expanduser("~/.launch-layout")

DEFAULT_TEMPLATE = """\
title: "REPLACE ME: LAYOUT TITLE"
# tab_color: yellow
# cwd: ~/path/to/project
#
# Available tab colors:
#   yellow, orange, red, magenta, violet, blue, cyan, green,
#   bright_green, gray, pink, teal, navy, brown, coral, purple
#   ...or use [r, g, b] for custom colors, e.g. [255, 100, 50]

root:
  split: rows
  sizes: [70, 30]
  children:
    - split: cols
      sizes: [60, 40]
      children:
        - command: echo hello world
        - {}
    - split: cols
      sizes: [40, 30, 30]
      children:
        - {}
        - {}
        - {}
"""


def get_layout_path(name: str) -> str:
    """Return the filesystem path for a named layout."""
    return os.path.join(LAYOUTS_DIR, f"{name}.yml")


def load_layout(name: str) -> dict:
    """Load and validate a YAML layout by name."""
    path = get_layout_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"layout not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "root" not in data:
        raise ValueError(f"invalid layout: missing 'root' key in {path}")
    errors = validate_tree(data["root"])
    if errors:
        raise ValueError(f"invalid layout {name}:\n" + "\n".join(f"  - {e}" for e in errors))
    return data


def validate_tree(node: object) -> list[str]:
    """Recursively validate a layout tree node. Returns a list of error messages."""
    errors: list[str] = []
    if not isinstance(node, dict):
        errors.append(f"node must be a dict, got {type(node).__name__}")
        return errors

    if "split" in node:
        split = node["split"]
        if split not in ("rows", "cols"):
            errors.append(f"invalid split: {split!r}, must be 'rows' or 'cols'")
        if "children" not in node:
            errors.append("split node missing 'children'")
            return errors
        if "sizes" not in node:
            errors.append("split node missing 'sizes'")
            return errors

        children = node["children"]
        sizes = node["sizes"]
        if not isinstance(children, list) or not isinstance(sizes, list):
            errors.append("'children' and 'sizes' must be lists")
            return errors
        if len(children) != len(sizes):
            errors.append(f"children count ({len(children)}) != sizes count ({len(sizes)})")
        if len(children) < 1:
            errors.append("split node must have at least 1 child")
        if not all(isinstance(s, (int, float)) and s > 0 for s in sizes):
            errors.append("sizes must be positive numbers")
        for child in children:
            errors.extend(validate_tree(child))

    return errors


def list_layouts() -> list[str]:
    """List available layout names (sans .yml extension)."""
    if not os.path.isdir(LAYOUTS_DIR):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(LAYOUTS_DIR) if f.endswith(".yml"))


def create_layout(name: str) -> str:
    """Write the default template for a new layout. Returns the file path."""
    os.makedirs(LAYOUTS_DIR, exist_ok=True)
    path = get_layout_path(name)
    if os.path.exists(path):
        raise FileExistsError(f"layout already exists: {path}")
    with open(path, "w") as f:
        f.write(DEFAULT_TEMPLATE)
    return path


def delete_layout(name: str) -> None:
    """Remove a layout YAML file."""
    path = get_layout_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"layout not found: {path}")
    os.remove(path)


def resolve_tab_color(value: object) -> tuple[int, int, int] | None:
    """Resolve a tab_color value to an (r, g, b) tuple, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        from lm import terminal

        if value not in terminal.TAB_COLORS:
            available = ", ".join(sorted(terminal.TAB_COLORS))
            raise ValueError(f"unknown tab color: {value!r}. Available: {available}")
        return terminal.TAB_COLORS[value]
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(value)
    raise ValueError(f"tab_color must be a color name or [r, g, b], got: {value!r}")
