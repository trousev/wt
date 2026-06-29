"""Ghostty backend — Terminal implementation using AppleScript."""

import shutil
import subprocess

from lm.terminal.base import Terminal


def _ghostty_escape(s: str) -> str:
    """Escape a string for AppleScript double‑quoted literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _ghostty_run_applescript(script: str) -> str:
    """Run an AppleScript via ``osascript`` and return stdout."""
    result = subprocess.run(
        ["osascript"],
        input=script,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(detail)
    return result.stdout.strip()


class GhosttyTerminal(Terminal):
    """Terminal implementation for Ghostty on macOS.

    .. note::
       Ghostty's AppleScript surface references are ephemeral — they only
       exist within the script that created them.  Therefore
       :meth:`apply_layout` is a monolithic operation that both splits
       panes **and** sends all leaf commands in one script execution.
       :meth:`send_input` is a no‑op for this backend.
    """

    # -- Terminal interface ---------------------------------------------------

    def is_available(self) -> bool:
        return (
            shutil.which("ghostty") is not None
            and shutil.which("osascript") is not None
        )

    def create_tab(self, title: str, cwd: str | None = None) -> str:
        title_esc = _ghostty_escape(title)
        script = f"""\
tell application "Ghostty"
    set win to front window
    perform action "new_tab" on (focused terminal of selected tab of win)
    delay 0.5
    set tabRef to selected tab of win
    perform action "set_tab_title:{title_esc}" on tabRef
    return id of tabRef as text
end tell"""
        return _ghostty_run_applescript(script)

    def apply_layout(
        self,
        tab_ref: str,
        layout_tree: dict,
        cwd: str | None = None,
    ) -> dict[str, str]:
        default_cwd = cwd
        sid_esc = _ghostty_escape(tab_ref)

        counter = [1]
        split_lines: list[str] = []
        cmd_lines: list[str] = []

        def next_var() -> str:
            name = f"s{counter[0]}"
            counter[0] += 1
            return name

        def walk(node: dict, var: str) -> None:
            if "split" not in node:
                leaf_cmd = node.get("command")
                leaf_cwd = node.get("cwd") or default_cwd
                if leaf_cmd and leaf_cwd:
                    text = f"cd {_ghostty_escape(leaf_cwd)} && {_ghostty_escape(leaf_cmd)}"
                    cmd_lines.append(f'    input text ("{text}" & return) to {var}')
                elif leaf_cmd:
                    text = _ghostty_escape(leaf_cmd)
                    cmd_lines.append(f'    input text ("{text}" & return) to {var}')
                elif leaf_cwd:
                    text = f"cd {_ghostty_escape(leaf_cwd)}"
                    cmd_lines.append(f'    input text ("{text}" & return) to {var}')
                return

            children = node["children"]
            sizes = node["sizes"]
            direction = "right" if node["split"] == "cols" else "down"
            total = sum(sizes)

            if len(children) == 1:
                walk(children[0], var)
                return

            new_var = next_var()
            cwd_esc = _ghostty_escape(default_cwd) if default_cwd else None

            split_lines.append(
                f"    set cfg_{new_var} to new surface configuration"
            )
            if cwd_esc:
                split_lines.append(
                    f'    set initial working directory of cfg_{new_var} to "{cwd_esc}"'
                )
            split_lines.append(
                f"    set {new_var} to split {var} direction {direction}"
                f" with configuration cfg_{new_var}"
            )

            walk(children[0], var)

            if len(children) == 2:
                walk(children[1], new_var)
            else:
                walk(
                    {
                        "split": node["split"],
                        "sizes": sizes[1:],
                        "children": children[1:],
                    },
                    new_var,
                )

        walk(layout_tree, "s0")

        script_parts = [
            'tell application "Ghostty"',
            "    repeat with w in every window",
            "        repeat with t in every tab of w",
            f'            if (id of t as text) is "{sid_esc}" then',
            "                set s0 to focused terminal of t",
        ]
        script_parts.extend(
            f"    {'    ' * 3}{line}" for line in split_lines
        )
        # Add indentation for the remaining lines inside the if block
        for line in cmd_lines:
            script_parts.append(f"    {'    ' * 3}{line}")
        script_parts.extend(
            [
                "            end if",
                "        end repeat",
                "    end repeat",
                "end tell",
            ]
        )

        _ghostty_run_applescript("\n".join(script_parts))
        return {}  # Ghostty surface references are ephemeral

    def send_input(self, pane_id: str, text: str) -> None:
        pass  # surface references are ephemeral; no‑op

    def set_tab_title(self, tab_ref: str, title: str) -> None:
        title_esc = _ghostty_escape(title)
        sid_esc = _ghostty_escape(tab_ref)
        script = f"""\
tell application "Ghostty"
    repeat with w in every window
        repeat with t in every tab of w
            if (id of t as text) is "{sid_esc}" then
                perform action "set_tab_title:{title_esc}" on (focused terminal of t)
                return
            end if
        end repeat
    end repeat
end tell"""
        try:
            _ghostty_run_applescript(script)
        except RuntimeError:
            pass

    def set_tab_color(
        self, tab_ref: str, color: tuple[int, int, int]
    ) -> None:
        pass  # Ghostty does not support tab colours via AppleScript

    def set_tab_icon(self, tab_ref: str, icon_path: str) -> bool:
        return False  # not supported

    def close_tab(self, tab_ref: str) -> None:
        sid_esc = _ghostty_escape(tab_ref)
        script = f"""\
tell application "Ghostty"
    repeat with w in every window
        repeat with t in every tab of w
            if (id of t as text) is "{sid_esc}" then
                perform action "close_tab" on (focused terminal of t)
                return
            end if
        end repeat
    end repeat
end tell"""
        try:
            _ghostty_run_applescript(script)
        except RuntimeError:
            pass

    def close_current_tab(self) -> None:
        script = """\
tell application "Ghostty"
    perform action "close_tab" on (focused terminal of selected tab of front window)
end tell"""
        try:
            _ghostty_run_applescript(script)
        except RuntimeError:
            pass

    def current_tab_ref(self) -> str | None:
        script = """\
tell application "Ghostty"
    set win to front window
    set tabRef to selected tab of win
    return id of tabRef as text
end tell"""
        try:
            return _ghostty_run_applescript(script)
        except RuntimeError:
            return None
