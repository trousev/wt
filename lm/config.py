"""Config management for lm — YAML-based configuration in ~/.lm.yaml."""

import os
import subprocess

import yaml

CONFIG_PATH = os.path.expanduser("~/.lm.yaml")
ICON_DIR = os.path.expanduser("~/.lm-icons")
REPO_REQUIRED_KEYS = ("run", "setup", "copy")


def get_icon_path(repo_name: str) -> str:
    """Return the icon file path for a repo: ~/.lm-icons/<owner>--<repo>.png."""
    return os.path.join(ICON_DIR, repo_name.replace("/", "--") + ".png")


def get_icon_path_if_exists(repo_name: str) -> str | None:
    """Return the icon file path if it exists on disk, else None.

    Checks the full owner--repo name first, then falls back to just the repo
    name (after the slash).  This lets `--repo shortcuts` match even when the
    auto-detected name is `trousev/shortcuts`.
    """
    path = get_icon_path(repo_name)
    if os.path.exists(path):
        return path
    if "/" in repo_name:
        short_path = os.path.join(ICON_DIR, repo_name.split("/", 1)[1] + ".png")
        if os.path.exists(short_path):
            return short_path
    return None


def normalize_script(value: str | list[str] | None) -> list[str]:
    """Normalize script value to list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _get_config_path() -> str:
    return CONFIG_PATH


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_repo_name() -> str | None:
    """Detect repo name from git origin (e.g., 'trousev/shortcuts')."""
    try:
        remote_url = _run_git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return None

    if remote_url.startswith("git@"):
        remote_url = remote_url[4:]
        if remote_url.endswith(".git"):
            remote_url = remote_url[:-4]
        if remote_url.startswith("github.com:"):
            remote_url = remote_url.replace("github.com:", "github.com/")
        if remote_url.startswith("github.com/"):
            remote_url = remote_url[len("github.com/") :]
        return remote_url
    elif remote_url.startswith("https://"):
        if remote_url.endswith(".git"):
            remote_url = remote_url[:-4]
        parts = remote_url.split("/")
        if len(parts) >= 2 and parts[-2] and parts[-1]:
            return f"{parts[-2]}/{parts[-1]}"

    return None


def load_config() -> dict:
    """Load config from YAML file. Returns empty dict if file doesn't exist."""
    try:
        with open(_get_config_path()) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def save_config(config: dict) -> None:
    """Save config to YAML file."""
    with open(_get_config_path(), "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)


def ensure_config_exists() -> None:
    """Create config file if it doesn't exist."""
    if not os.path.exists(_get_config_path()):
        save_config({})


def ensure_repo_in_config(repo_name: str) -> None:
    """Ensure repo has all required keys (run, setup, copy). Create empty if missing."""
    config = load_config()

    if repo_name not in config:
        config[repo_name] = {}

    for key in REPO_REQUIRED_KEYS:
        if key not in config[repo_name]:
            config[repo_name][key] = None

    save_config(config)


def get_repo_settings(repo_name: str | None = None) -> dict:
    """Get settings for a repo. If repo_name is None, detect from git origin."""
    if repo_name is None:
        repo_name = get_repo_name()
        if repo_name is None:
            return {}

    config = load_config()
    repo_config = config.get(repo_name, {})

    defaults = {
        "run": None,
        "setup": None,
        "copy": [],
    }

    for key, default_value in defaults.items():
        if key not in repo_config:
            repo_config[key] = default_value
        elif key in ("run", "setup") and repo_config[key] is not None:
            repo_config[key] = normalize_script(repo_config[key])

    return repo_config


def has_repo_setting(repo_name: str | None, key: str) -> bool:
    """Check if a setting was explicitly provided in config (not defaulted)."""
    if repo_name is None:
        repo_name = get_repo_name()
        if repo_name is None:
            return False

    config = load_config()
    repo_config = config.get(repo_name, {})
    return key in repo_config


def get_current_repo_settings() -> dict:
    """Get settings for the current repository (detected from git origin)."""
    return get_repo_settings()


def edit_config() -> None:
    """Open config file in editor."""
    editor = os.environ.get("EDITOR", "vim")
    os.execvp(editor, [editor, _get_config_path()])


def validate_config() -> list[str]:
    """Validate config YAML and return list of warnings."""
    warnings = []
    try:
        config = load_config()
    except yaml.YAMLError as e:
        return [f"YAML syntax error: {e}"]

    if not isinstance(config, dict):
        return ["Config must be a YAML dictionary"]

    for repo_name, repo_config in config.items():
        if repo_name == "general":
            continue

        if not isinstance(repo_config, dict):
            warnings.append(f"Repo '{repo_name}': config must be a dictionary")
            continue

        for key in ("run", "setup"):
            if key not in repo_config:
                warnings.append(f"Repo '{repo_name}': missing '{key}' field")
            else:
                value = repo_config[key]
                if value is not None and not isinstance(value, str) and not isinstance(value, list):
                    warnings.append(
                        f"Repo '{repo_name}': '{key}' must be a string or list of strings"
                    )

        if "copy" in repo_config:
            if not isinstance(repo_config["copy"], list):
                warnings.append(f"Repo '{repo_name}': 'copy' must be a list")

    return warnings
