"""Cmux backend — Terminal implementation using the cmux CLI.

cmux is a Ghostty-based macOS terminal with a CLI and socket API.
https://cmux.com/docs/api
"""

import json
import re
import shutil
import subprocess

from lm.terminal.base import Terminal, _get_coding_agent, _save_pane_info


def _parse_ref(output: str, prefix: str) -> str:
    """Extract *prefix*:N from a cmux CLI output line like ``OK surface:5 workspace:4``."""
    m = re.search(rf"(?<!\w){prefix}:\d+", output)
    return m.group(0) if m else ""


def _layout_to_cmux(node: dict, default_cwd: str | None = None) -> dict:
    """Convert our layout format to cmux's ``new-workspace --layout`` JSON."""
    if "split" not in node:
        leaf_cmd = node.get("command")
        leaf_cwd = node.get("cwd") or default_cwd
        cmd = None
        if leaf_cmd and leaf_cwd:
            cmd = f"cd {leaf_cwd} && {leaf_cmd}"
        elif leaf_cmd:
            cmd = leaf_cmd
        elif leaf_cwd:
            cmd = f"cd {leaf_cwd}"
        surface: dict[str, object] = {"type": "terminal"}
        if cmd:
            surface["command"] = cmd
        return {"pane": {"surfaces": [surface]}}

    children = node["children"]
    sizes = node["sizes"]
    direction = "horizontal" if node["split"] == "cols" else "vertical"

    if len(children) == 1:
        return _layout_to_cmux(children[0], default_cwd)

    total = sum(sizes)

    cmux_children = [_layout_to_cmux(children[0], default_cwd)]

    if len(children) == 2:
        cmux_children.append(_layout_to_cmux(children[1], default_cwd))
    else:
        cmux_children.append(
            _layout_to_cmux(
                {
                    "split": node["split"],
                    "sizes": sizes[1:],
                    "children": children[1:],
                },
                default_cwd,
            )
        )

    return {
        "direction": direction,
        "split": sizes[0] / total,
        "children": cmux_children,
    }


class CmuxTerminal(Terminal):
    """Terminal implementation for cmux on macOS.

    Uses the ``cmux`` CLI under the hood.  Layouts are realised by
    passing the full tree to ``cmux new-workspace --layout`` which
    handles proportional sizing natively.
    """

    def __init__(self) -> None:
        super().__init__()
        self._last_ws_ref: str | None = None

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
        try:
            self._run(["close-workspace", "--workspace", tab_ref])
        except subprocess.CalledProcessError:
            pass

        cmux_layout = _layout_to_cmux(layout_tree, default_cwd=cwd)

        ws_args = ["workspace", "create", "--layout", json.dumps(cmux_layout), "--focus", "true"]
        if cwd:
            ws_args.extend(["--cwd", cwd])
        output = self._run(ws_args)
        self._last_ws_ref = _parse_ref(output, "workspace")
        if not self._last_ws_ref:
            raise RuntimeError(f"could not parse workspace ref from: {output}")

        data = self._run_json(["list-panels", "--workspace", self._last_ws_ref])
        surfaces = sorted(data.get("surfaces", []), key=lambda s: s["index"])

        result: dict[str, str] = {}
        for i, s in enumerate(surfaces):
            result[f"s{i}"] = s["ref"]
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
        ref = self._last_ws_ref or tab_ref
        self._run(["close-workspace", "--workspace", ref])

    def close_current_tab(self) -> None:
        self._run(["close-workspace"])

    def current_tab_ref(self) -> str | None:
        try:
            data = self._run_json(["current-workspace"])
            return data.get("workspace_ref")
        except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
            return None

    # -- cmux-specific overrides ---------------------------------------------

    def build_layout(
        self,
        wt_path: str,
        pane_title: str,
        tab_color: tuple[int, int, int] | None = None,
        first_pane_command: str | None = None,
        icon_path: str | None = None,
        bottom_left_command: str | None = None,
    ) -> str:
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

        cmux_layout = _layout_to_cmux(layout, default_cwd=wt_path)

        ws_args = [
            "workspace",
            "create",
            "--layout",
            json.dumps(cmux_layout),
            "--cwd",
            wt_path,
            "--focus",
            "true",
        ]
        if pane_title:
            ws_args.extend(["--name", pane_title])
        output = self._run(ws_args)
        tab_ref = _parse_ref(output, "workspace")
        if not tab_ref:
            raise RuntimeError(f"could not parse workspace ref from: {output}")
        self._last_ws_ref = tab_ref

        data = self._run_json(["list-panels", "--workspace", tab_ref])
        surfaces = sorted(data.get("surfaces", []), key=lambda s: s["index"])

        pane_map: dict[str, str] = {}
        for i, s in enumerate(surfaces):
            pane_map[f"s{i}"] = s["ref"]

        if tab_color:
            self.set_tab_color(tab_ref, tab_color)
        if icon_path:
            self.set_tab_icon(tab_ref, icon_path)

        _save_pane_info(wt_path, pane_map, tab_ref)
        return tab_ref
