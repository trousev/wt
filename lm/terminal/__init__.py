"""Terminal emulator automation — interface (Terminal ABC) and
per‑provider implementations (iTerm2, WezTerm, Ghostty).

Quick start
-----------

.. code-block:: python

    from lm.terminal import autodetect_terminal

    t = autodetect_terminal()
    ref = t.create_tab("my project")
    t.set_tab_icon(ref, "/path/to/icon.png")

For backward compatibility the module also exposes module‑level functions
that internally call ``autodetect_terminal()`` and dispatch:

.. code-block:: python

    from lm import terminal

    terminal.build_layout(...)
"""

from lm.terminal.base import (
    TAB_COLORS,
    Terminal,
    _get_coding_agent,
    _get_worktrees_dir,
    _load_pane_info,
    _remove_pane_info,
    _save_pane_info,
    detect,
)
from lm.terminal.ghostty import GhosttyTerminal
from lm.terminal.iterm2 import Iterm2Terminal
from lm.terminal.wezterm import WezTermTerminal

#: Backward‑compat alias
get_current_terminal = detect

_TERMINAL_BACKEND_MAP: dict[str, type[Terminal]] = {
    "iTerm2": Iterm2Terminal,
    "WezTerm": WezTermTerminal,
    "Ghostty": GhosttyTerminal,
}


def autodetect_terminal() -> Terminal | None:
    """Detect the running terminal emulator and return an instance of the
    matching :class:`Terminal` implementation.

    Returns ``None`` when the terminal cannot be identified or is not
    supported.
    """
    name = detect()
    if name is None:
        return None
    cls = _TERMINAL_BACKEND_MAP.get(name)
    if cls is None:
        return None
    return cls()


def _require_terminal() -> Terminal:
    """Return an autodetected terminal or raise ``RuntimeError``."""
    t = autodetect_terminal()
    if t is None:
        raise RuntimeError("no supported terminal emulator found")
    return t


# ---------------------------------------------------------------------------
# Module‑level backward‑compatibility wrappers
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """``True`` if we are inside a supported terminal and its tools are ready."""
    t = autodetect_terminal()
    if t is None:
        return False
    return t.is_available()


def build_layout(
    wt_path: str,
    pane_title: str,
    tab_color: tuple[int, int, int] | None = None,
    first_pane_command: str | None = None,
    icon_path: str | None = None,
    bottom_left_command: str | None = None,
) -> str:
    """Create a multi‑pane tab layout.  Returns the tab reference."""
    return _require_terminal().build_layout(
        wt_path,
        pane_title,
        tab_color,
        first_pane_command,
        icon_path,
        bottom_left_command,
    )


def update_tab_status(
    pane_title: str,
    tab_color: tuple[int, int, int],
    session_id: str | None = None,
    icon_path: str | None = None,
) -> None:
    """Update pane titles and tab colour on all panes in the originating tab."""
    _require_terminal().update_tab_status(
        pane_title, tab_color, session_id=session_id, icon_path=icon_path
    )


def rename_pane_titles(new_title: str, session_id: str | None = None) -> None:
    """Update all pane titles in the originating tab."""
    _require_terminal().rename_pane_titles(new_title, session_id=session_id)


def close_current_tab() -> None:
    """Close the current terminal tab."""
    _require_terminal().close_current_tab()


def kill_worktree_panes(wt_path: str) -> None:
    """Kill all panes (or close the tab) associated with a worktree."""
    _require_terminal().kill_worktree_panes(wt_path)


def build_generic_layout(
    tree: dict,
    default_cwd: str | None = None,
    title: str | None = None,
    tab_color: tuple[int, int, int] | None = None,
) -> None:
    """Create a multi‑pane tab layout from a tree definition."""
    _require_terminal().build_generic_layout(tree, default_cwd, title, tab_color)
