from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

ExecutableCheck = Callable[[Path], bool]
PathLookup = Callable[[str], str | None]


def is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def platform_candidates(
    platform: str,
    environ: Mapping[str, str],
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    if platform == "darwin":
        return (
            (
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ),
            (
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
                "microsoft-edge",
                "msedge",
            ),
        )
    if platform == "win32":
        roots = tuple(
            Path(value)
            for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if (value := environ.get(name, "").strip())
        )
        fixed = tuple(
            root / relative
            for root in roots
            for relative in (
                Path("Google/Chrome/Application/chrome.exe"),
                Path("Chromium/Application/chrome.exe"),
                Path("Microsoft/Edge/Application/msedge.exe"),
            )
        )
        return fixed, ("chrome.exe", "chromium.exe", "msedge.exe")
    return (
        (
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/microsoft-edge"),
            Path("/usr/bin/microsoft-edge-stable"),
        ),
        (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "microsoft-edge-stable",
            "msedge",
        ),
    )


def _first_executable(
    paths: tuple[Path, ...] | list[Path],
    is_executable: ExecutableCheck,
) -> Path | None:
    for path in paths:
        if is_executable(path):
            return path
    return None


def discover_browser(
    explicit_path: str | Path | None = None,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    is_executable: ExecutableCheck = is_executable_file,
    which: PathLookup = shutil.which,
) -> Path | None:
    """Find Chrome, Chromium, or Edge with explicit > environment > platform precedence."""
    environment = os.environ if environ is None else environ
    if explicit_path is not None and str(explicit_path).strip():
        return _first_executable(
            [Path(explicit_path).expanduser()],
            is_executable,
        )

    environment_path = environment.get("MULTI_SEARCH_CHROME_PATH", "").strip()
    if environment_path:
        return _first_executable(
            [Path(environment_path).expanduser()],
            is_executable,
        )

    fixed_candidates, executable_names = platform_candidates(
        platform or sys.platform,
        environment,
    )
    selected = _first_executable(list(fixed_candidates), is_executable)
    if selected is not None:
        return selected

    discovered = [
        Path(resolved)
        for name in executable_names
        if (resolved := which(name)) is not None
    ]
    return _first_executable(discovered, is_executable)
