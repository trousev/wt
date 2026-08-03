"""Kitty backend — Terminal implementation using ``kitty @`` remote control.

Kitty is a fast, feature-rich GPU-based terminal emulator.
https://sw.kovidgoyal.net/kitty/

Requires ``allow_remote_control yes`` in ``kitty.conf`` (or started with
``-o allow_remote_control=yes``) for external ``kitty @`` calls.
When run *inside* a kitty window, ``kitty @`` can reach the controlling
terminal without a listen socket.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from lm.terminal.base import Terminal, _get_coding_agent, _save_pane_info


def _flatten_tree(node: dict, default_cwd: str | None = None) -> list[dict]:
    """Return a flat list of leaf descriptors in DFS order.

    Each leaf is ``{"command": str|None, "cwd": str|None}``.
    """
    if "split" not in node:
        cmd = node.get("command")
        cwd = node.get("cwd") or default_cwd

        full_cmd: str | None = None
        if cmd:
            full_cmd = cmd
        return [{"command": full_cmd, "cwd": cwd}]

    result: list[dict] = []
    for child in node["children"]:
        result.extend(_flatten_tree(child, default_cwd))
    return result


class KittyTerminal(Terminal):
    """Terminal implementation for Kitty on Linux/macOS.

    Uses the ``kitty @`` CLI under the hood.  Tab references are
    numeric window IDs — Kitty's match system resolves ``--match id:N``
    to the containing tab when the ID belongs to a window, so a single
    window ID serves as both a window and tab anchor.
    """

    def __init__(self) -> None:
        super().__init__()
        self._kitty_bin: str | None = None

    def _resolve_kitty(self) -> str:
        """Locate the ``kitty`` binary, caching the result."""
        if self._kitty_bin:
            return self._kitty_bin

        # 1. Try PATH first (kitty is the main binary, almost always present).
        found = shutil.which("kitty")
        if found:
            self._kitty_bin = found
            return found

        # 2. Fall back to KITTY_INSTALLATION_DIR (set by every kitty window).
        install_dir = os.environ.get("KITTY_INSTALLATION_DIR", "")
        if install_dir:
            candidate = os.path.join(install_dir, "bin", "kitty")
            if os.path.isfile(candidate):
                self._kitty_bin = candidate
                return candidate

        # 3. Not found — _run will fail with a clear error.
        return "kitty"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, args: list[str]) -> str:
        """Run ``kitty @`` with *args*, return stripped stdout."""
        try:
            result = subprocess.run(
                [self._resolve_kitty(), "@"] + args,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            if stderr:
                # Augment common failure modes with actionable advice.
                if "allow_remote_control" in stderr.lower():
                    raise RuntimeError(
                        f"kitty remote control is disabled.  "
                        f"Add ``allow_remote_control yes`` to your kitty.conf.\n"
                        f"kitty error: {stderr}"
                    ) from exc
                if "permission" in stderr.lower():
                    raise RuntimeError(
                        f"kitty remote control permission denied.  "
                        f"Check ``allow_remote_control`` / ``remote_control_password`` in kitty.conf.\n"
                        f"kitty error: {stderr}"
                    ) from exc
                raise RuntimeError(f"kitty @ failed: {stderr}") from exc
            raise RuntimeError(
                f"kitty @ failed with exit code {exc.returncode}"
            ) from exc
        return result.stdout.strip()

    def _run_json(self, args: list[str]) -> object:
        """Run ``kitty @`` and parse the response as JSON."""
        return json.loads(self._run(args))

    # ------------------------------------------------------------------
    # Terminal interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        try:
            return os.path.isfile(self._resolve_kitty())
        except Exception:
            return False

    def _find_tab_id(self, window_id: str) -> str:
        """Return the tab ID that contains *window_id*.

        Raises RuntimeError if the window cannot be found in any tab
        (which usually means ``--type=tab`` didn't create a new tab).
        """
        try:
            raw = self._run(["ls"])
            data = json.loads(raw)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to list kitty windows (kitten @ ls): {exc}"
            ) from exc

        if not isinstance(data, list):
            raise RuntimeError(
                f"Unexpected kitty @ ls output (expected JSON array): {raw!r}"
            )

        # Find the tab that contains this window.
        for os_win in data:
            for tab in os_win.get("tabs", []):
                for win in tab.get("windows", []):
                    if str(win.get("id")) == window_id:
                        return str(tab["id"])

        # Window not found — dump the ls output for debugging.
        import pprint
        raise RuntimeError(
            f"Window {window_id!r} not found in kitty @ ls output:\n"
            f"{pprint.pformat(data)}"
        )

    # -- tab lifecycle ---------------------------------------------------

    def create_tab(self, title: str, cwd: str | None = None) -> str:
        """Create a new tab, returning its *tab* ID."""
        args: list[str] = ["launch", "--type", "tab"]
        if title:
            args.extend(["--tab-title", title])
        if cwd:
            args.extend(["--cwd", cwd])
        win_id = self._run(args)
        return self._find_tab_id(win_id)

    def close_tab(self, tab_ref: str) -> None:
        try:
            self._run(["close-tab", "--match", f"id:{tab_ref}"])
        except Exception:
            pass

    def close_current_tab(self) -> None:
        try:
            self._run(["close-tab", "--self"])
        except Exception:
            pass

    def current_tab_ref(self) -> str | None:
        """Return the ID of the currently focused tab."""
        try:
            data = self._run_json(["ls"])
        except Exception:
            return None

        if not isinstance(data, list):
            return None
        for os_win in data:
            for tab in os_win.get("tabs", []):
                for win in tab.get("windows", []):
                    if win.get("is_focused"):
                        return str(tab["id"])
        return None

    # -- layout ----------------------------------------------------------

    def _create_splits(
        self,
        node: dict,
        tab_id: str,
        default_cwd: str | None,
        *,
        first_window: bool = True,
        next_to: str | None = None,
        split_dir: str | None = None,
        bias: float | None = None,
    ) -> list[str]:
        """Recursively create windows for *node*.  Returns window IDs in DFS order."""
        if "split" not in node:
            # Leaf — create a window.
            cmd = node.get("command")
            cwd = node.get("cwd") or default_cwd

            if first_window:
                args: list[str] = ["launch", "--type", "tab", "--keep-focus"]
                if cwd:
                    args.extend(["--cwd", cwd])
                win_id = self._run(args)
                if cmd:
                    try:
                        self.send_input(win_id, cmd + "\n")
                    except Exception:
                        pass
            else:
                args = [
                    "launch",
                    "--match", f"id:{tab_id}",
                    "--keep-focus",
                    "--location", split_dir,
                    "--next-to", f"id:{next_to}",
                ]
                if bias is not None:
                    args.extend(["--bias", str(round(bias * 100))])
                if cwd:
                    args.extend(["--cwd", cwd])
                win_id = self._run(args)
                if cmd:
                    try:
                        self.send_input(win_id, cmd + "\n")
                    except Exception:
                        pass

            return [win_id]

        # Internal node — process children.
        direction = "vsplit" if node["split"] == "cols" else "hsplit"
        children = node["children"]
        sizes = node["sizes"]
        total = sum(sizes)

        all_wins: list[str] = []
        for i, child in enumerate(children):
            if first_window and i == 0:
                # First child in first group — may start a new tab.
                wins = self._create_splits(
                    child, tab_id, default_cwd,
                    first_window=True,
                )
            else:
                # Split from the first child of this group.
                ref = all_wins[0] if i > 0 else next_to
                child_bias = None
                if len(children) == 2:
                    child_bias = sizes[1] / (sizes[0] + sizes[1]) if i > 0 else sizes[0] / total
                elif len(children) > 2:
                    remaining = sum(sizes[i:])
                    child_bias = remaining / (sizes[i - 1] + remaining) if i > 0 else sizes[0] / total

                wins = self._create_splits(
                    child, tab_id, default_cwd,
                    first_window=False,
                    next_to=ref,
                    split_dir=direction,
                    bias=child_bias if i > 0 else None,
                )
            all_wins.extend(wins)

        return all_wins

    def apply_layout(
        self,
        tab_ref: str,
        layout_tree: dict,
        cwd: str | None = None,
    ) -> dict[str, str]:
        """Apply a layout tree to an existing tab.

        *tab_ref* is a kitty tab ID.  Returns ``{s0: window_id, s1: …}``
        in DFS order.
        """
        windows = self._create_splits(layout_tree, tab_ref, default_cwd=cwd)
        pane_map: dict[str, str] = {}
        for i, wid in enumerate(windows):
            pane_map[f"s{i}"] = wid

        try:
            self._run(["goto-layout", "--match", f"id:{tab_ref}", "splits"])
        except Exception:
            pass

        return pane_map

    # -- I/O -------------------------------------------------------------

    def send_input(self, pane_id: str, text: str) -> None:
        self._run(["send-text", "--match", f"id:{pane_id}", text])

    # -- title / color / icon --------------------------------------------

    def set_tab_title(self, tab_ref: str, title: str) -> None:
        try:
            self._run(["set-tab-title", "--match", f"id:{tab_ref}", title])
        except Exception:
            pass

    def set_tab_color(self, tab_ref: str, color: tuple[int, int, int]) -> None:
        # kitty tab colors only affect the tab-bar chrome, not the tab
        # content, and even that is unreliable across versions — skip it.
        pass

    def set_tab_icon(self, tab_ref: str, icon_path: str) -> bool:
        return False  # not supported by kitty

    # -- composite overrides ---------------------------------------------

    def build_layout(
        self,
        wt_path: str,
        pane_title: str,
        tab_color: tuple[int, int, int] | None = None,
        first_pane_command: str | None = None,
        icon_path: str | None = None,
        bottom_left_command: str | None = None,
    ) -> str:
        """One-shot 5-pane worktree layout — see :meth:`Terminal.build_layout`."""
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

        leaves = _flatten_tree(layout, default_cwd=wt_path)

        # --- first window → new tab (shell only, no command) -----------
        first = leaves[0]
        args: list[str] = ["launch", "--type", "tab", "--keep-focus"]
        if pane_title:
            args.extend(["--tab-title", pane_title])
        if first.get("cwd"):
            args.extend(["--cwd", first["cwd"]])

        first_win_id = self._run(args)
        tab_id = self._find_tab_id(first_win_id)
        pane_map: dict[str, str] = {"s0": first_win_id}

        # Send the command into s0 after the tab is alive.
        if first.get("command"):
            try:
                self.send_input(first_win_id, first["command"] + "\n")
            except Exception:
                pass

        # --- remaining windows with explicit splits -------------------
        # Layout: rows [70,30] → top: cols [60,40] {s0,s1} / bot: cols [40,30,30] {s2,s3,s4}
        #
        # Split order matters!  hsplit FIRST to carve out the bottom row,
        # THEN vsplit the top, THEN vsplit the bottom twice.
        #
        #   s0 (W0): first window, tab anchor
        #   s2 (W2): hsplit from s0 → bottom row, bias 30
        #   s1 (W1): vsplit from s0 → right of top row, bias 40
        #   s3 (W3): vsplit from s2 → right of bottom row, bias 43
        #   s4 (W4): vsplit from s3 → right of bottom row, bias 50
        #
        # Note: kitty --bias is in percent (0–100), NOT fraction!

        leaf_to_win: dict[int, str] = {0: first_win_id}  # s0 → W0

        split_plan = [
            # (leaf_idx, location, ref_leaf_idx, bias_pct)
            (2,  "hsplit",      0,           30),    # s2: hsplit from s0
            (1,  "vsplit",      0,           40),    # s1: vsplit from s0
            (3,  "vsplit",      2,           43),    # s3: vsplit from s2
            (4,  "vsplit",      2,           50),    # s4: vsplit from s2 (both from s2)
        ]

        for leaf_idx, location, ref_leaf_idx, bias_pct in split_plan:
            leaf = leaves[leaf_idx]
            ref_win = leaf_to_win[ref_leaf_idx]
            win_args: list[str] = [
                "launch",
                "--match", f"id:{tab_id}",
                "--keep-focus",
                "--location", location,
                "--next-to", f"id:{ref_win}",
                "--bias", str(bias_pct),
            ]
            if leaf.get("cwd"):
                win_args.extend(["--cwd", leaf["cwd"]])
            # Never pass a command to launch — use send-text instead,
            # otherwise the window closes when the command exits.
            win_id = self._run(win_args)
            leaf_to_win[leaf_idx] = win_id
            if leaf.get("command"):
                try:
                    self.send_input(win_id, leaf["command"] + "\n")
                except Exception:
                    pass

        for leaf_idx, win_id in leaf_to_win.items():
            pane_map[f"s{leaf_idx}"] = win_id

        # Let the splits layout engine distribute space.
        try:
            self._run(["goto-layout", "--match", f"id:{tab_id}", "splits"])
        except Exception:
            pass

        if tab_color:
            self.set_tab_color(tab_id, tab_color)
        if icon_path:
            self.set_tab_icon(tab_id, icon_path)

        _save_pane_info(wt_path, pane_map, tab_id)
        return tab_id
