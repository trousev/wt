"""Cmux backend — Terminal implementation using the cmux CLI.

cmux is a Ghostty-based macOS terminal with a CLI and socket API.
https://cmux.com/docs/api
"""

import json
import re
import shutil
import subprocess
import time

from lm.terminal.base import Terminal


def _parse_ref(output: str, prefix: str) -> str:
    """Extract *prefix*:N from a cmux CLI output line like ``OK surface:5 workspace:4``."""
    m = re.search(rf"(?<!\w){prefix}:\d+", output)
    return m.group(0) if m else ""


class CmuxTerminal(Terminal):
    """Terminal implementation for cmux on macOS.

    Uses the ``cmux`` CLI under the hood.  Layouts are realised by
    sequencing ``new-split`` calls on the targeted workspace via
    ``--workspace``.
    """

    @staticmethod
    def _run(args: list[str]) -> str:
        result = subprocess.run(
            ["cmux"] + args,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    @staticmethod
    def _run_json(args: list[str]) -> dict:
        raw = json.loads(CmuxTerminal._run(args + ["--json"]))
        assert isinstance(raw, dict), f"expected dict, got {type(raw).__name__}"
        return raw

    # -- Terminal interface ---------------------------------------------------

    def is_available(self) -> bool:
        return shutil.which("cmux") is not None

    def create_tab(self, title: str, cwd: str | None = None) -> str:
        output = self._run(["workspace", "create"])
        ws_id = _parse_ref(output, "workspace")
        if not ws_id:
            raise RuntimeError(f"could not parse workspace ref from: {output}")
        return ws_id

    def apply_layout(
        self,
        tab_ref: str,
        layout_tree: dict,
        cwd: str | None = None,
    ) -> dict[str, str]:
        default_cwd = cwd
        time.sleep(0.3)

        initial = self._current_surface(tab_ref)
        if not initial:
            raise RuntimeError(f"could not find any surface in workspace {tab_ref}")

        leaves: list[tuple[str, str | None, str | None]] = []

        def walk(node: dict, surface_id: str) -> None:
            if "split" not in node:
                leaves.append((surface_id, node.get("command"), node.get("cwd")))
                return

            children = node["children"]
            if len(children) == 1:
                walk(children[0], surface_id)
                return

            direction = "right" if node["split"] == "cols" else "down"

            self._run(["focus-panel", "--panel", surface_id, "--workspace", tab_ref])
            time.sleep(0.1)

            output = self._run(["new-split", direction, "--workspace", tab_ref])
            time.sleep(0.2)

            new_surface = _parse_ref(output, "surface")
            if not new_surface:
                raise RuntimeError(f"could not parse surface ref from: {output}")

            walk(children[0], surface_id)

            if len(children) == 2:
                walk(children[1], new_surface)
            else:
                walk(
                    {
                        "split": node["split"],
                        "sizes": node["sizes"][1:],
                        "children": children[1:],
                    },
                    new_surface,
                )

        walk(layout_tree, initial)

        result: dict[str, str] = {}
        for i, (sid, command, leaf_cwd) in enumerate(leaves):
            label = f"s{i}"
            result[label] = sid
            effective_cwd = leaf_cwd or default_cwd
            text = ""
            if command and effective_cwd:
                text = f"cd {effective_cwd} && {command}\n"
            elif command:
                text = f"{command}\n"
            elif effective_cwd:
                text = f"cd {effective_cwd}\n"
            if text:
                self._run(["send", "--surface", sid, text])

        return result

    def send_input(self, pane_id: str, text: str) -> None:
        self._run(["send", "--surface", pane_id, text])

    def set_tab_title(self, tab_ref: str, title: str) -> None:
        try:
            self._run(
                [
                    "workspace-action",
                    "--action",
                    "set-description",
                    "--description",
                    title,
                    "--workspace",
                    tab_ref,
                ]
            )
        except subprocess.CalledProcessError:
            pass

    def set_tab_color(self, tab_ref: str, color: tuple[int, int, int]) -> None:
        hex_color = "#{:02x}{:02x}{:02x}".format(*color)
        try:
            self._run(
                [
                    "workspace-action",
                    "--action",
                    "set-color",
                    "--color",
                    hex_color,
                    "--workspace",
                    tab_ref,
                ]
            )
        except subprocess.CalledProcessError:
            pass

    def set_tab_icon(self, tab_ref: str, icon_path: str) -> bool:
        return False

    def _current_surface(self, ws_ref: str | None = None) -> str:
        args = ["list-panels", "--json"]
        if ws_ref:
            args.extend(["--workspace", ws_ref])
        data = self._run_json(args)
        for s in data.get("surfaces", []):
            if s.get("focused"):
                return s["ref"]
        items = data.get("surfaces", [])
        return items[0]["ref"] if items else ""

    def close_tab(self, tab_ref: str) -> None:
        self._run(["close-workspace", "--workspace", tab_ref])

    def close_current_tab(self) -> None:
        self._run(["close-workspace"])

    def current_tab_ref(self) -> str | None:
        try:
            data = self._run_json(["current-workspace"])
            return data.get("workspace_ref")
        except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
            return None
