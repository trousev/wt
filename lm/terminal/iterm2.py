"""iTerm2 backend — Terminal implementation using the iTerm2 Python API."""

import json
import os
import shutil
import subprocess

from lm.terminal.base import Terminal


class Iterm2Terminal(Terminal):
    """Terminal implementation for iTerm2 on macOS."""

    @staticmethod
    def _python() -> str:
        """Return the Python interpreter that ``it2`` bundles (has the ``iterm2`` package)."""
        it2_path = shutil.which("it2")
        if not it2_path:
            raise RuntimeError("it2 not found on PATH")
        with open(it2_path) as f:
            first_line = f.readline().strip()
        if first_line.startswith("#!"):
            return first_line[2:].strip()
        raise RuntimeError("could not determine it2's Python interpreter")

    @staticmethod
    def _run_script(script: str) -> str:
        """Run an iTerm2 Python async script and return stdout."""
        python = Iterm2Terminal._python()
        result = subprocess.run(
            [python, "-c", script],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise RuntimeError(detail)
        return result.stdout.strip()

    _FIND_TAB = """\
async def _iterm2_find_tab(app, tab_id):
    for window in app.terminal_windows:
        for tab in window.tabs:
            if tab.tab_id == tab_id:
                return tab
    window = app.current_terminal_window
    if window:
        return window.current_tab
    return None
"""

    # -- Terminal interface ---------------------------------------------------

    def is_available(self) -> bool:
        return shutil.which("it2") is not None

    def create_tab(self, title: str, cwd: str | None = None) -> str:
        script = f"""\
import iterm2
import iterm2.profile

{self._FIND_TAB}

async def main(connection):
    app = await iterm2.async_get_app(connection)
    window = app.current_terminal_window
    if not window:
        raise RuntimeError("no iTerm2 window found")
    tab = await window.async_create_tab()

    for s in tab.sessions:
        profile = iterm2.LocalWriteOnlyProfile()
        profile.set_title_components([iterm2.TitleComponents.SESSION_NAME])
        profile.set_allow_title_setting(False)
        await s.async_set_profile_properties(profile)
        await s.async_set_name({title!r})

    print(tab.tab_id)

iterm2.run_until_complete(main)
"""
        return self._run_script(script)

    def apply_layout(
        self,
        tab_ref: str,
        layout_tree: dict,
        cwd: str | None = None,
    ) -> dict[str, str]:
        default_cwd = cwd
        counter = [1]
        split_lines: list[str] = []
        cmd_lines: list[str] = []
        pane_vars = ["s0"]

        def next_var() -> str:
            name = f"s{counter[0]}"
            counter[0] += 1
            return name

        def walk(node: dict, var: str) -> None:
            if "split" not in node:
                leaf_cmd = node.get("command")
                leaf_cwd = node.get("cwd") or default_cwd
                if leaf_cmd and leaf_cwd:
                    text = f"cd {leaf_cwd} && {leaf_cmd}\n"
                    cmd_lines.append(f"    await {var}.async_send_text({text!r})")
                elif leaf_cmd:
                    cmd_lines.append(f"    await {var}.async_send_text({leaf_cmd!r})")
                elif leaf_cwd:
                    text = f"cd {leaf_cwd}\n"
                    cmd_lines.append(f"    await {var}.async_send_text({text!r})")
                return

            children = node["children"]
            sizes = node["sizes"]
            is_vertical = node["split"] == "cols"
            total = sum(sizes)

            if len(children) == 1:
                walk(children[0], var)
                return

            new_var = next_var()
            pane_vars.append(new_var)
            split_lines.append(
                f"    {new_var} = await {var}.async_split_pane(vertical={is_vertical})"
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

        all_vars_str = ", ".join(pane_vars)
        session_items = ", ".join(f"{v!r}: {v}.session_id" for v in pane_vars)

        lines = [
            "import iterm2",
            "import iterm2.profile",
            "import json",
            "",
            self._FIND_TAB.strip(),
            "",
            "async def main(connection):",
            "    app = await iterm2.async_get_app(connection)",
            f"    tab = await _iterm2_find_tab(app, {tab_ref!r})",
            "    if not tab:",
            f"        raise RuntimeError('tab {tab_ref!r} not found')",
            "    s0 = tab.current_session",
            "",
        ]
        lines.extend(split_lines)
        if split_lines:
            lines.append("")

        lines.extend(
            [
                f"    for s in [{all_vars_str}]:",
                "        profile = iterm2.LocalWriteOnlyProfile()",
                "        profile.set_title_components([iterm2.TitleComponents.SESSION_NAME])",
                "        profile.set_allow_title_setting(False)",
                "        await s.async_set_profile_properties(profile)",
                "",
            ]
        )

        lines.extend(cmd_lines)
        if cmd_lines:
            lines.append("")

        lines.append(f"    print(json.dumps({{{session_items}}}))")
        lines.append("")
        lines.append("iterm2.run_until_complete(main)")

        raw = self._run_script("\n".join(lines))
        return json.loads(raw)

    def send_input(self, pane_id: str, text: str) -> None:
        script = f"""\
import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)
    session = app.get_session_by_id({pane_id!r})
    if session:
        await session.async_send_text({text!r})

iterm2.run_until_complete(main)
"""
        self._run_script(script)

    def set_tab_title(self, tab_ref: str, title: str) -> None:
        script = f"""\
import iterm2
import iterm2.profile

{self._FIND_TAB}

async def main(connection):
    app = await iterm2.async_get_app(connection)
    tab = await _iterm2_find_tab(app, {tab_ref!r})
    if not tab:
        return
    for s in tab.sessions:
        await s.async_set_name({title!r})

iterm2.run_until_complete(main)
"""
        self._run_script(script)

    def set_tab_color(self, tab_ref: str, color: tuple[int, int, int]) -> None:
        r, g, b = color
        script = f"""\
import iterm2
import iterm2.profile

{self._FIND_TAB}

async def main(connection):
    app = await iterm2.async_get_app(connection)
    tab = await _iterm2_find_tab(app, {tab_ref!r})
    if not tab:
        return
    for s in tab.sessions:
        tc = iterm2.LocalWriteOnlyProfile()
        tc.set_tab_color(iterm2.Color({r}, {g}, {b}))
        tc.set_use_tab_color(True)
        await s.async_set_profile_properties(tc)

iterm2.run_until_complete(main)
"""
        self._run_script(script)

    def set_tab_icon(self, tab_ref: str, icon_path: str) -> bool:
        script = f"""\
import iterm2
import iterm2.profile

{self._FIND_TAB}

async def main(connection):
    app = await iterm2.async_get_app(connection)
    tab = await _iterm2_find_tab(app, {tab_ref!r})
    if not tab:
        return
    for s in tab.sessions:
        ic = iterm2.LocalWriteOnlyProfile()
        ic.set_icon_mode(iterm2.profile.IconMode.CUSTOM)
        ic.set_custom_icon_path({icon_path!r})
        await s.async_set_profile_properties(ic)

iterm2.run_until_complete(main)
"""
        self._run_script(script)
        return True

    def close_tab(self, tab_ref: str) -> None:
        script = f"""\
import iterm2

{self._FIND_TAB}

async def main(connection):
    app = await iterm2.async_get_app(connection)
    tab = await _iterm2_find_tab(app, {tab_ref!r})
    if tab:
        await tab.async_close()

iterm2.run_until_complete(main)
"""
        self._run_script(script)

    def close_current_tab(self) -> None:
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "iTerm2" to close current tab of current window',
            ],
            check=False,
        )

    def current_tab_ref(self) -> str | None:
        script = f"""\
import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)
    window = app.current_terminal_window
    if window and window.current_tab:
        print(window.current_tab.tab_id)

iterm2.run_until_complete(main)
"""
        try:
            return self._run_script(script)
        except RuntimeError:
            return None
