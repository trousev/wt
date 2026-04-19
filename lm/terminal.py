"""terminal — terminal emulator automation (iTerm2 layout, pane titles, tab colors)."""

import json
import os
import shutil
import subprocess

TAB_COLORS = {
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
    """Return the coding agent to launch. Uses LM_CODING_AGENT env var if set, otherwise defaults to 'claude'."""
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


def _iterm2_is_available() -> bool:
    return shutil.which("it2") is not None


def _iterm2_python() -> str:
    """Return the Python interpreter that it2 uses (has iterm2 package)."""
    it2_path = shutil.which("it2")
    if not it2_path:
        raise RuntimeError("it2 not found on PATH")
    with open(it2_path) as f:
        first_line = f.readline().strip()
    if first_line.startswith("#!"):
        return first_line[2:].strip()
    raise RuntimeError("could not determine it2's Python interpreter")


def _iterm2_run_script(script: str) -> str:
    """Run an iterm2 async script via it2's Python interpreter. Returns stdout."""
    python = _iterm2_python()
    result = subprocess.run(
        [python, "-c", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(detail)
    return result.stdout.strip()


def _iterm2_build_layout(
    wt_path: str,
    pane_title: str,
    tab_color: tuple[int, int, int] | None = None,
    first_pane_command: str | None = None,
    icon_path: str | None = None,
) -> str:
    agent = first_pane_command if first_pane_command else _get_coding_agent()
    script = f"""\
import iterm2
import iterm2.profile

async def main(connection):
    app = await iterm2.async_get_app(connection)
    window = app.current_terminal_window
    if not window:
        raise RuntimeError("no iTerm2 window found")

    # Save window frame so we can restore it after layout changes
    frame = await window.async_get_frame()

    # Create a new tab — s0 is the initial session
    tab = await window.async_create_tab()
    s0 = tab.current_session

    # Split: top (s0) / bottom (s1)
    s1 = await s0.async_split_pane(vertical=False)
    # Split top row: top-left (s0) / top-right (s2)
    s2 = await s0.async_split_pane(vertical=True)
    # Split bottom row: bottom-left (s1) / bottom-right (s3)
    s3 = await s1.async_split_pane(vertical=True)
    # Split bottom-right: bottom-mid (s3) / bottom-right (s4)
    s4 = await s3.async_split_pane(vertical=True)

    # Configure all panes: set title and cd into worktree
    wt_path = {wt_path!r}
    title = {pane_title!r}
    for s in (s0, s1, s2, s3, s4):
        # Show only session name as the pane title
        profile = iterm2.LocalWriteOnlyProfile()
        profile.set_title_components([iterm2.TitleComponents.SESSION_NAME])
        profile.set_allow_title_setting(False)
        await s.async_set_profile_properties(profile)
        await s.async_set_name(title)
        if s is s0:
            await s.async_send_text(f"cd {{wt_path}} && {agent}\\n")
        elif s is s3:
            await s.async_send_text(f"cd {{wt_path}} && lm setup\\n")
        elif s is s4:
            await s.async_send_text(f"cd {{wt_path}} && lm pull --watch\\n")
        else:
            await s.async_send_text(f"cd {{wt_path}}\\n")

    # Resize panes to desired proportions
    # Target: top 70% / bottom 30%, top 60|40, bottom 40|30|30
    total_cols = s0.grid_size.width + s2.grid_size.width
    total_rows = s0.grid_size.height + s1.grid_size.height

    top_rows = int(total_rows * 0.70)
    bot_rows = total_rows - top_rows

    sizes = {{
        s0: (int(total_cols * 0.60), top_rows),
        s2: (total_cols - int(total_cols * 0.60), top_rows),
        s1: (int(total_cols * 0.40), bot_rows),
        s3: (int(total_cols * 0.30), bot_rows),
        s4: (total_cols - int(total_cols * 0.40) - int(total_cols * 0.30), bot_rows),
    }}
    for sess, (w, h) in sizes.items():
        sess.preferred_size = iterm2.util.Size(w, h)

    await tab.async_update_layout()
    await window.async_set_frame(frame)

    # Set tab color on every session so it persists regardless of focus
    tab_color = {tab_color!r}
    if tab_color:
        r, g, b = tab_color
        for s in (s0, s1, s2, s3, s4):
            tc = iterm2.LocalWriteOnlyProfile()
            tc.set_tab_color(iterm2.Color(r, g, b))
            tc.set_use_tab_color(True)
            await s.async_set_profile_properties(tc)

    # Set custom tab icon if provided
    icon_path = {icon_path!r}
    if icon_path:
        for s in (s0, s1, s2, s3, s4):
            ic = iterm2.LocalWriteOnlyProfile()
            ic.set_icon_mode(iterm2.profile.IconMode.CUSTOM)
            ic.set_custom_icon_path(icon_path)
            await s.async_set_profile_properties(ic)

    # Focus the top-left pane where Claude runs
    await s0.async_activate()

    # Print tab_id so the caller can capture it for cleanup
    print(tab.tab_id)

iterm2.run_until_complete(main)
"""
    tab_id = _iterm2_run_script(script)
    _save_pane_info(wt_path, {}, tab_id)
    return tab_id


_FIND_TAB_BY_SESSION = """\
async def find_tab_by_session(app, session_id):
    if session_id:
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if session.session_id == session_id:
                        return tab
    window = app.current_terminal_window
    if window:
        return window.current_tab
    return None
"""


def _iterm2_update_tab_status(
    pane_title: str,
    tab_color: tuple[int, int, int],
    session_id: str | None = None,
    icon_path: str | None = None,
) -> None:
    script = f"""\
import iterm2
import iterm2.profile

{_FIND_TAB_BY_SESSION}

async def main(connection):
    app = await iterm2.async_get_app(connection)
    tab = await find_tab_by_session(app, {session_id!r})
    if not tab:
        return

    title = {pane_title!r}
    r, g, b = {tab_color!r}
    for s in tab.sessions:
        await s.async_set_name(title)
        tc = iterm2.LocalWriteOnlyProfile()
        tc.set_tab_color(iterm2.Color(r, g, b))
        tc.set_use_tab_color(True)
        await s.async_set_profile_properties(tc)

    icon_path = {icon_path!r}
    if icon_path:
        for s in tab.sessions:
            ic = iterm2.LocalWriteOnlyProfile()
            ic.set_icon_mode(iterm2.profile.IconMode.CUSTOM)
            ic.set_custom_icon_path(icon_path)
            await s.async_set_profile_properties(ic)

iterm2.run_until_complete(main)
"""
    _iterm2_run_script(script)


def _iterm2_rename_pane_titles(new_title: str, session_id: str | None = None) -> None:
    script = f"""\
import iterm2

{_FIND_TAB_BY_SESSION}

async def main(connection):
    app = await iterm2.async_get_app(connection)
    tab = await find_tab_by_session(app, {session_id!r})
    if not tab:
        return
    title = {new_title!r}
    for s in tab.sessions:
        await s.async_set_name(title)

iterm2.run_until_complete(main)
"""
    _iterm2_run_script(script)


def _iterm2_close_current_tab() -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application "iTerm2" to close current tab of current window'],
        check=False,
    )


def _wezterm_is_available() -> bool:
    return shutil.which("wezterm") is not None


def _wezterm_run(args: list[str]) -> str:
    result = subprocess.run(
        ["wezterm", "cli"] + args,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _wezterm_build_layout(
    wt_path: str,
    pane_title: str,
    tab_color: tuple[int, int, int] | None = None,
    first_pane_command: str | None = None,
    icon_path: str | None = None,
) -> str:
    agent = first_pane_command if first_pane_command else _get_coding_agent()
    s0 = _wezterm_run(["spawn", "--cwd", wt_path])

    s1 = _wezterm_run(["split-pane", "--pane-id", s0, "--bottom", "--percent", "30"])

    s2 = _wezterm_run(["split-pane", "--pane-id", s0, "--right", "--percent", "40"])

    s3 = _wezterm_run(["split-pane", "--pane-id", s1, "--right", "--percent", "60"])

    s4 = _wezterm_run(["split-pane", "--pane-id", s3, "--right", "--percent", "50"])

    pane_ids = {"s0": s0, "s1": s1, "s2": s2, "s3": s3, "s4": s4}

    _wezterm_run(["send-text", "--pane-id", s0, f"{agent}\n"])
    _wezterm_run(["send-text", "--pane-id", s3, "lm setup\n"])
    _wezterm_run(["send-text", "--pane-id", s4, "lm pull --watch\n"])

    _wezterm_run(["activate-pane-direction", "--pane-id", s0, "Up"])

    tabs = _wezterm_run(["list"]).strip().split("\n")
    tab_id = None
    if tabs:
        first_line = tabs[-1].split()
        if len(first_line) >= 2:
            tab_id = first_line[1]

    if tab_id:
        _wezterm_run(["set-tab-title", "--tab-id", tab_id, pane_title])

    _save_pane_info(wt_path, pane_ids, tab_id)

    if tabs:
        first_line = tabs[-1].split()
        if len(first_line) >= 2:
            return first_line[2]

    return ""


def _wezterm_update_tab_status(
    pane_title: str,
    tab_color: tuple[int, int, int],
    session_id: str | None = None,
    icon_path: str | None = None,
) -> None:
    pane_id = session_id
    if not pane_id:
        return

    tabs = _wezterm_run(["list"]).strip().split("\n")
    tab_id = None
    for line in tabs:
        parts = line.split()
        if len(parts) >= 3 and parts[2] == pane_id:
            tab_id = parts[1]
            break

    if tab_id:
        _wezterm_run(["set-tab-title", "--tab-id", tab_id, pane_title])


def _wezterm_rename_pane_titles(new_title: str, session_id: str | None = None) -> None:
    pane_id = session_id
    if not pane_id:
        return

    _wezterm_run(["set-tab-title", "--pane-id", pane_id, new_title])


def _wezterm_close_current_tab() -> None:
    subprocess.run(
        ["wezterm", "cli", "kill-tab"],
        check=False,
    )


def _wezterm_kill_worktree_panes(wt_path: str) -> None:
    """Kill all panes associated with a worktree."""
    pane_info = _load_pane_info(wt_path)
    if not pane_info or "panes" not in pane_info:
        return

    pane_ids = list(pane_info["panes"].values())
    if not pane_ids:
        return

    script = "\n".join(
        f'subprocess.run(["wezterm", "cli", "kill-pane", "--pane-id", "{pane_id}"], check=False)'
        for pane_id in pane_ids
    )
    subprocess.Popen(
        ["python3", "-c", f"import subprocess; {script}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    _remove_pane_info(wt_path)


def _iterm2_kill_worktree_panes(wt_path: str) -> None:
    """iTerm2: close the tab containing the worktree's panes."""
    pane_info = _load_pane_info(wt_path)
    if not pane_info or "tab_id" not in pane_info:
        return

    tab_id = pane_info["tab_id"]
    script = f"""\
import iterm2

async def main(connection):
    app = await iterm2.async_get_app(connection)
    for window in app.terminal_windows:
        for tab in window.tabs:
            if tab.tab_id == {tab_id!r}:
                await tab.async_close()
                break

iterm2.run_until_complete(main)
"""
    _iterm2_run_script(script)
    _remove_pane_info(wt_path)


def _walk_tree_iterm2(
    tree: dict, default_cwd: str | None
) -> tuple[list[str], list[str], list[tuple]]:
    """Walk a layout tree and generate iTerm2 script fragments.

    Returns (split_lines, all_vars, leaves) where leaves are
    (var, command, cwd, w_frac, h_frac) tuples.
    """
    counter = [1]
    splits: list[str] = []
    leaves: list[tuple] = []
    split_vars: list[str] = []

    def next_var() -> str:
        name = f"s{counter[0]}"
        counter[0] += 1
        return name

    def walk(node: dict, var: str, w_frac: float, h_frac: float) -> None:
        if "split" not in node:
            leaves.append((var, node.get("command"), node.get("cwd"), w_frac, h_frac))
            return

        children = node["children"]
        sizes = node["sizes"]
        is_vertical = node["split"] == "cols"
        total = sum(sizes)

        if len(children) == 1:
            walk(children[0], var, w_frac, h_frac)
            return

        first_frac = sizes[0] / total
        rest_frac = 1 - first_frac
        new_var = next_var()
        split_vars.append(new_var)
        splits.append(f"    {new_var} = await {var}.async_split_pane(vertical={is_vertical})")

        if is_vertical:
            first_w, first_h = w_frac * first_frac, h_frac
            rest_w, rest_h = w_frac * rest_frac, h_frac
        else:
            first_w, first_h = w_frac, h_frac * first_frac
            rest_w, rest_h = w_frac, h_frac * rest_frac

        walk(children[0], var, first_w, first_h)

        if len(children) == 2:
            walk(children[1], new_var, rest_w, rest_h)
        else:
            walk(
                {"split": node["split"], "sizes": sizes[1:], "children": children[1:]},
                new_var,
                rest_w,
                rest_h,
            )

    walk(tree, "s0", 1.0, 1.0)
    all_vars = ["s0"] + split_vars
    return splits, all_vars, leaves


def _iterm2_build_generic_layout(
    tree: dict,
    default_cwd: str | None = None,
    title: str | None = None,
    tab_color: tuple[int, int, int] | None = None,
) -> None:
    splits, all_vars, leaves = _walk_tree_iterm2(tree, default_cwd)
    all_vars_str = ", ".join(all_vars)

    lines = [
        "import iterm2",
        "",
        "async def main(connection):",
        "    app = await iterm2.async_get_app(connection)",
        "    window = app.current_terminal_window",
        "    if not window:",
        "        raise RuntimeError('no iTerm2 window found')",
        "",
        "    frame = await window.async_get_frame()",
        "    tab = await window.async_create_tab()",
        "    s0 = tab.current_session",
        "",
    ]

    lines.extend(splits)
    if splits:
        lines.append("")

    if title:
        lines.extend(
            [
                f"    for s in [{all_vars_str}]:",
                "        profile = iterm2.LocalWriteOnlyProfile()",
                "        profile.set_title_components([iterm2.TitleComponents.SESSION_NAME])",
                "        profile.set_allow_title_setting(False)",
                "        await s.async_set_profile_properties(profile)",
                f"        await s.async_set_name({title!r})",
                "",
            ]
        )

    for var, cmd, cwd, _w, _h in leaves:
        effective_cwd = cwd or default_cwd
        if cmd and effective_cwd:
            text = f"cd {effective_cwd} && {cmd}\n"
        elif cmd:
            text = cmd + "\n"
        elif effective_cwd:
            text = f"cd {effective_cwd}\n"
        else:
            continue
        lines.append(f"    await {var}.async_send_text({text!r})")
    lines.append("")

    for var, _cmd, _cwd, w_frac, h_frac in leaves:
        w = max(1, round(100 * w_frac))
        h = max(1, round(100 * h_frac))
        lines.append(f"    {var}.preferred_size = iterm2.util.Size({w}, {h})")
    lines.extend(
        [
            "    await tab.async_update_layout()",
            "    await window.async_set_frame(frame)",
            "",
        ]
    )

    if tab_color:
        r, g, b = tab_color
        lines.extend(
            [
                f"    for s in [{all_vars_str}]:",
                "        tc = iterm2.LocalWriteOnlyProfile()",
                f"        tc.set_tab_color(iterm2.Color({r}, {g}, {b}))",
                "        tc.set_use_tab_color(True)",
                "        await s.async_set_profile_properties(tc)",
                "",
            ]
        )

    first_var = leaves[0][0] if leaves else "s0"
    lines.extend(
        [
            f"    await {first_var}.async_activate()",
            "",
            "iterm2.run_until_complete(main)",
        ]
    )

    _iterm2_run_script("\n".join(lines))


def _wezterm_build_generic_layout(
    tree: dict,
    default_cwd: str | None = None,
    title: str | None = None,
    tab_color: tuple[int, int, int] | None = None,
) -> None:
    spawn_args = ["spawn"]
    if default_cwd:
        spawn_args.extend(["--cwd", default_cwd])
    first_pane = _wezterm_run(spawn_args)

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
        new_pane = _wezterm_run(
            ["split-pane", "--pane-id", pane_id, direction, "--percent", rest_pct]
        )

        walk(children[0], pane_id)

        if len(children) == 2:
            walk(children[1], new_pane)
        else:
            walk(
                {"split": node["split"], "sizes": sizes[1:], "children": children[1:]},
                new_pane,
            )

    walk(tree, first_pane)

    for pane_id, cmd, cwd in leaves:
        if cwd:
            text = f"cd {cwd} && {cmd}\n" if cmd else f"cd {cwd}\n"
            _wezterm_run(["send-text", "--pane-id", pane_id, text])
        elif cmd:
            _wezterm_run(["send-text", "--pane-id", pane_id, f"{cmd}\n"])

    if title:
        _wezterm_run(["set-tab-title", "--pane-id", first_pane, title])

    if leaves:
        try:
            _wezterm_run(["activate-pane", "--pane-id", leaves[0][0]])
        except subprocess.CalledProcessError:
            pass


_BACKENDS = {
    "iterm2": {
        "is_available": _iterm2_is_available,
        "build_layout": _iterm2_build_layout,
        "build_generic_layout": _iterm2_build_generic_layout,
        "update_tab_status": _iterm2_update_tab_status,
        "rename_pane_titles": _iterm2_rename_pane_titles,
        "close_current_tab": _iterm2_close_current_tab,
        "kill_worktree_panes": _iterm2_kill_worktree_panes,
    },
    "wezterm": {
        "is_available": _wezterm_is_available,
        "build_layout": _wezterm_build_layout,
        "build_generic_layout": _wezterm_build_generic_layout,
        "update_tab_status": _wezterm_update_tab_status,
        "rename_pane_titles": _wezterm_rename_pane_titles,
        "close_current_tab": _wezterm_close_current_tab,
        "kill_worktree_panes": _wezterm_kill_worktree_panes,
    },
}


def _get_backend() -> str | None:
    """Detect the first available terminal backend."""
    for name, funcs in _BACKENDS.items():
        if funcs["is_available"]():
            return name
    return None


def is_available() -> bool:
    """True if any supported terminal backend is detected."""
    return _get_backend() is not None


def build_layout(
    wt_path: str,
    pane_title: str,
    tab_color: tuple[int, int, int] | None = None,
    first_pane_command: str | None = None,
    icon_path: str | None = None,
) -> str:
    """Create a multi-pane tab layout. Returns tab_id.

    If first_pane_command is provided, it will be run in the first pane instead of the default agent.
    If icon_path is provided, a custom tab icon will be set (iTerm2 only).
    """
    backend = _get_backend()
    if not backend:
        raise RuntimeError("no supported terminal emulator found")
    return _BACKENDS[backend]["build_layout"](
        wt_path, pane_title, tab_color, first_pane_command, icon_path=icon_path
    )


def update_tab_status(
    pane_title: str,
    tab_color: tuple[int, int, int],
    session_id: str | None = None,
    icon_path: str | None = None,
) -> None:
    """Update pane titles and tab color on all panes in the originating tab."""
    backend = _get_backend()
    if not backend:
        raise RuntimeError("no supported terminal emulator found")
    _BACKENDS[backend]["update_tab_status"](
        pane_title, tab_color, session_id=session_id, icon_path=icon_path
    )


def rename_pane_titles(new_title: str, session_id: str | None = None) -> None:
    """Update all pane titles in the originating tab."""
    backend = _get_backend()
    if not backend:
        raise RuntimeError("no supported terminal emulator found")
    _BACKENDS[backend]["rename_pane_titles"](new_title, session_id=session_id)


def close_current_tab() -> None:
    """Close the current terminal tab."""
    backend = _get_backend()
    if not backend:
        raise RuntimeError("no supported terminal emulator found")
    _BACKENDS[backend]["close_current_tab"]()


def kill_worktree_panes(wt_path: str) -> None:
    """Kill all panes associated with a worktree."""
    backend = _get_backend()
    if not backend:
        raise RuntimeError("no supported terminal emulator found")
    _BACKENDS[backend]["kill_worktree_panes"](wt_path)


def build_generic_layout(
    tree: dict,
    default_cwd: str | None = None,
    title: str | None = None,
    tab_color: tuple[int, int, int] | None = None,
) -> None:
    """Create a multi-pane tab layout from a tree definition."""
    backend = _get_backend()
    if not backend:
        raise RuntimeError("no supported terminal emulator found")
    _BACKENDS[backend]["build_generic_layout"](tree, default_cwd, title, tab_color)
