"""WezTerm backend — Terminal implementation using the wezterm CLI."""

import os
import shutil
import subprocess

from lm.terminal.base import Terminal


class WezTermTerminal(Terminal):
    """Terminal implementation for WezTerm (cross‑platform)."""

    @staticmethod
    def _run(args: list[str]) -> str:
        result = subprocess.run(
            ["wezterm", "cli"] + args,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def _list_tabs(self) -> list[dict[str, str]]:
        """Parse ``wezterm cli list`` output.

        Returns a list of dicts with keys ``win_id``, ``tab_id``, ``pane_id``,
        ``workspace`` (truncated parsing — only the first 3 numeric columns
        matter for our purposes).
        """
        raw = self._run(["list"])
        lines = raw.strip().split("\n")
        result: list[dict[str, str]] = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            # skip header line (columns start with a digit)
            if not parts[0].isdigit():
                continue
            result.append(
                {
                    "win_id": parts[0],
                    "tab_id": parts[1],
                    "pane_id": parts[2],
                }
            )
        return result

    def _find_tab_panes(self, tab_ref: str) -> list[str]:
        """Return all pane IDs that belong to a given tab."""
        return [
            row["pane_id"]
            for row in self._list_tabs()
            if row["tab_id"] == tab_ref
        ]

    def _find_first_pane_for_tab(self, tab_ref: str) -> str | None:
        """Return any pane ID belonging to the tab (used as a handle for splits)."""
        panes = self._find_tab_panes(tab_ref)
        return panes[0] if panes else None

    def _find_tab_for_pane(self, pane_id: str) -> str | None:
        """Find the tab ID that contains a given pane."""
        for row in self._list_tabs():
            if row["pane_id"] == pane_id:
                return row["tab_id"]
        return None

    # -- Terminal interface ---------------------------------------------------

    def is_available(self) -> bool:
        return shutil.which("wezterm") is not None

    def create_tab(self, title: str, cwd: str | None = None) -> str:
        spawn_args = ["spawn"]
        if cwd:
            spawn_args.extend(["--cwd", cwd])
        pane_id = self._run(spawn_args)

        tab_id = self._find_tab_for_pane(pane_id)
        if not tab_id:
            return pane_id  # fallback

        self._run(["set-tab-title", "--tab-id", tab_id, title])
        return tab_id

    def apply_layout(
        self,
        tab_ref: str,
        layout_tree: dict,
        cwd: str | None = None,
    ) -> dict[str, str]:
        first_pane = self._find_first_pane_for_tab(tab_ref)
        if not first_pane:
            return {}

        counter = [0]
        leaves: list[tuple[str, str | None, str | None]] = []

        def walk(node: dict, pane_id: str) -> None:
            if "split" not in node:
                leaves.append((pane_id, node.get("command"), node.get("cwd")))
                return

            children = node["children"]
            sizes = node["sizes"]
            direction = "--right" if node["split"] == "cols" else "--bottom"
            total = sum(sizes)

            if len(children) == 1:
                walk(children[0], pane_id)
                return

            rest_pct = str(round((total - sizes[0]) / total * 100))
            new_pane = self._run(
                [
                    "split-pane",
                    "--pane-id",
                    pane_id,
                    direction,
                    "--percent",
                    rest_pct,
                ]
            )

            walk(children[0], pane_id)

            if len(children) == 2:
                walk(children[1], new_pane)
            else:
                walk(
                    {
                        "split": node["split"],
                        "sizes": sizes[1:],
                        "children": children[1:],
                    },
                    new_pane,
                )

        walk(layout_tree, first_pane)

        result: dict[str, str] = {}
        for i, (pane_id, command, leaf_cwd) in enumerate(leaves):
            label = f"s{i}"
            result[label] = pane_id
            effective_cwd = leaf_cwd or cwd
            if command and effective_cwd:
                self._run(
                    [
                        "send-text",
                        "--pane-id",
                        pane_id,
                        f"cd {effective_cwd} && {command}\n",
                    ]
                )
            elif command:
                self._run(["send-text", "--pane-id", pane_id, f"{command}\n"])
            elif effective_cwd:
                self._run(
                    ["send-text", "--pane-id", pane_id, f"cd {effective_cwd}\n"]
                )

        return result

    def send_input(self, pane_id: str, text: str) -> None:
        self._run(["send-text", "--pane-id", pane_id, text])

    def set_tab_title(self, tab_ref: str, title: str) -> None:
        self._run(["set-tab-title", "--tab-id", tab_ref, title])

    def set_tab_color(
        self, tab_ref: str, color: tuple[int, int, int]
    ) -> None:
        pass  # WezTerm does not support tab colours via CLI

    def set_tab_icon(self, tab_ref: str, icon_path: str) -> bool:
        return False  # not supported

    def close_tab(self, tab_ref: str) -> None:
        panes = self._find_tab_panes(tab_ref)
        for pane_id in panes:
            subprocess.run(
                ["wezterm", "cli", "kill-pane", "--pane-id", pane_id],
                capture_output=True,
                text=True,
                check=False,
            )

    def close_current_tab(self) -> None:
        subprocess.run(["wezterm", "cli", "kill-tab"], check=False)

    def current_tab_ref(self) -> str | None:
        tabs = self._list_tabs()
        if not tabs:
            return None
        return tabs[-1]["tab_id"]
