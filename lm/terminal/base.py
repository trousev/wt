"""Terminal ABC — interface for terminal emulator automation."""

import json
import os
from abc import ABC, abstractmethod

from lm.config import get_general_setting

TAB_COLORS: dict[str, tuple[int, int, int]] = {
    "yellow": (181, 137, 0),
    "orange": (203, 75, 22),
    "red": (220, 50, 47),
    "magenta": (211, 54, 130),
    "violet": (108, 113, 196),
    "blue": (38, 139, 210),
    "cyan": (42, 161, 152),
    "green": (133, 153, 0),
    "bright_green": (42, 161, 52),
    "gray": (88, 110, 117),
    "pink": (255, 105, 180),
    "teal": (0, 128, 128),
    "navy": (0, 43, 112),
    "brown": (150, 100, 50),
    "coral": (255, 127, 80),
    "purple": (128, 0, 128),
}


def _get_coding_agent() -> str:
    """Return the coding agent to launch. Checks config first, then env var, then defaults."""
    config_value = get_general_setting("coding_agent")
    if config_value:
        return config_value
    return os.environ.get("LM_CODING_AGENT", "claude")


def _get_worktrees_dir() -> str:
    return os.path.expanduser("~/.worktrees")


def _get_pane_info_path(wt_path: str) -> str:
    """Return the path to the pane info JSON file for a worktree."""
    dirname = os.path.basename(os.path.realpath(wt_path))
    return os.path.join(_get_worktrees_dir(), f"{dirname}.json")


def _save_pane_info(wt_path: str, pane_ids: dict[str, str], tab_id: str | None = None) -> None:
    """Save pane IDs and tab ID to a JSON file."""
    path = _get_pane_info_path(wt_path)
    data: dict = {"panes": pane_ids}
    if tab_id:
        data["tab_id"] = tab_id
    with open(path, "w") as f:
        json.dump(data, f)


def _load_pane_info(wt_path: str) -> dict | None:
    """Load pane info from JSON file, or None if not found."""
    path = _get_pane_info_path(wt_path)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _remove_pane_info(wt_path: str) -> None:
    """Remove the pane info JSON file."""
    path = _get_pane_info_path(wt_path)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def detect() -> str | None:
    """Detect the current terminal emulator from environment variables.

    Checks TERM_PROGRAM first (most authoritative), then falls back to
    other env-var heuristics.  Returns None when the terminal cannot be
    identified.
    """
    term_program = os.environ.get("TERM_PROGRAM", "")

    # cmux sets TERM_PROGRAM=ghostty, so check cmux-specific env vars first
    if "CMUX_WORKSPACE_ID" in os.environ or "CMUX_SURFACE_ID" in os.environ:
        return "Cmux"

    if term_program == "iTerm.app":
        return "iTerm2"
    if term_program == "WezTerm":
        return "WezTerm"
    if term_program == "Ghostty":
        return "Ghostty"
    if term_program == "Apple_Terminal":
        return "Apple_Terminal"
    if term_program == "kitty":
        return "kitty"
    if term_program == "vscode":
        return "vscode"

    if "TMUX" in os.environ:
        return "tmux"
    if "WEZTERM_PANE" in os.environ:
        return "WezTerm"
    if "ITERM_SESSION_ID" in os.environ:
        return "iTerm2"
    if "GHOSTTY_RESOURCES_DIR" in os.environ:
        return "Ghostty"
    return None


class Terminal(ABC):
    """Abstract interface for terminal emulator automation.

    Granular API — each backend implements these methods independently.
    Composite convenience methods (build_layout, update_tab_status, …)
    are provided with default implementations that delegate to the
    granular methods.
    """

    TAB_COLORS = TAB_COLORS

    # --- lifecycle -----------------------------------------------------------

    @abstractmethod
    def create_tab(self, title: str, cwd: str | None = None) -> str:
        """Create a new tab.

        *title* is the initial tab title.
        *cwd* is the initial working directory (backend-dependent).

        Returns an opaque *tab reference* that can be passed to
        apply_layout, set_tab_title, close_tab, etc.
        """

    @abstractmethod
    def close_tab(self, tab_ref: str) -> None:
        """Close a tab previously returned by *create_tab*."""

    @abstractmethod
    def close_current_tab(self) -> None:
        """Close the currently focused tab."""

    # --- layout --------------------------------------------------------------

    @abstractmethod
    def apply_layout(
        self,
        tab_ref: str,
        layout_tree: dict,
        cwd: str | None = None,
    ) -> dict[str, str]:
        """Split *tab_ref* into panes according to *layout_tree*.

        *layout_tree* follows the format defined in ``lm.layout_engine``::

            {
                "split": "rows" | "cols",
                "sizes": [int, …],
                "children": [node, …],
            }

        A leaf node has optional ``command`` and ``cwd`` keys.
        If both are present the backend sends ``cd {cwd} && {command}``
        to that pane; if only *cwd* is present it sends ``cd {cwd}``.

        Returns a ``{label: pane_id}`` mapping for every leaf in the
        tree (labels follow DFS order: ``s0``, ``s1``, …).
        """

    # --- input ---------------------------------------------------------------

    @abstractmethod
    def send_input(self, pane_id: str, text: str) -> None:
        """Send *text* to a pane (identified by an id from *apply_layout*)."""

    # --- tab appearance ------------------------------------------------------

    @abstractmethod
    def set_tab_title(self, tab_ref: str, title: str) -> None:
        """Set the title of a tab."""

    @abstractmethod
    def set_tab_color(self, tab_ref: str, color: tuple[int, int, int]) -> None:
        """Set the tab colour.  A no‑op if the terminal doesn't support it."""

    @abstractmethod
    def set_tab_icon(self, tab_ref: str, icon_path: str) -> bool:
        """Set a custom tab icon.  Returns ``True`` on success, ``False`` if
        the terminal does not support custom tab icons."""

    # --- queries -------------------------------------------------------------

    @abstractmethod
    def current_tab_ref(self) -> str | None:
        """Return the opaque reference of the currently focused tab, or
        ``None`` if it cannot be determined."""

    @abstractmethod
    def is_available(self) -> bool:
        """``True`` if this terminal's tooling is installed and usable."""

    # --- composite convenience methods (with default implementations) --------

    def build_layout(
        self,
        wt_path: str,
        pane_title: str,
        tab_color: tuple[int, int, int] | None = None,
        first_pane_command: str | None = None,
        icon_path: str | None = None,
        bottom_left_command: str | None = None,
    ) -> str:
        """Create a tab with the standard opinionated 5‑pane layout.

        Returns the tab reference (opaque string).
        """
        agent = first_pane_command if first_pane_command else _get_coding_agent()

        layout: dict = {
            "split": "rows",
            "sizes": [70, 30],
            "children": [
                {
                    "split": "cols",
                    "sizes": [60, 40],
                    "children": [
                        {"command": agent, "cwd": wt_path},
                        {"cwd": wt_path},
                    ],
                },
                {
                    "split": "cols",
                    "sizes": [40, 30, 30],
                    "children": [
                        (
                            {"command": bottom_left_command, "cwd": wt_path}
                            if bottom_left_command
                            else {"cwd": wt_path}
                        ),
                        {"command": "lm setup", "cwd": wt_path},
                        {"command": "lm pull --watch", "cwd": wt_path},
                    ],
                },
            ],
        }

        tab_ref = self.create_tab(pane_title, cwd=wt_path)
        pane_map = self.apply_layout(tab_ref, layout, cwd=wt_path)

        if tab_color:
            self.set_tab_color(tab_ref, tab_color)
        if icon_path:
            self.set_tab_icon(tab_ref, icon_path)

        _save_pane_info(wt_path, pane_map, tab_ref)
        return tab_ref

    def build_generic_layout(
        self,
        tree: dict,
        default_cwd: str | None = None,
        title: str | None = None,
        tab_color: tuple[int, int, int] | None = None,
    ) -> None:
        """Create a tab with an arbitrary layout tree."""
        tab_ref = self.create_tab(title or "layout", cwd=default_cwd)
        self.apply_layout(tab_ref, tree, cwd=default_cwd)
        if tab_color:
            self.set_tab_color(tab_ref, tab_color)

    def update_tab_status(
        self,
        pane_title: str,
        tab_color: tuple[int, int, int],
        session_id: str | None = None,
        icon_path: str | None = None,
    ) -> None:
        """Update tab title, colour and optional icon.

        *session_id* is the tab reference for backward compatibility.
        """
        if session_id:
            self.set_tab_title(session_id, pane_title)
            self.set_tab_color(session_id, tab_color)
            if icon_path:
                self.set_tab_icon(session_id, icon_path)

    def rename_pane_titles(self, new_title: str, session_id: str | None = None) -> None:
        """Update the tab title.

        *session_id* is the tab reference for backward compatibility.
        """
        if session_id:
            self.set_tab_title(session_id, new_title)

    def kill_worktree_panes(self, wt_path: str) -> None:
        """Close the tab associated with a worktree."""
        pane_info = _load_pane_info(wt_path)
        if pane_info and "tab_id" in pane_info:
            self.close_tab(pane_info["tab_id"])
        _remove_pane_info(wt_path)
