"""Version information for Sugar"""

import tomllib
from pathlib import Path

try:
    from importlib.metadata import version as get_package_version
except ImportError:
    from importlib_metadata import version as get_package_version


def _get_version() -> str:
    """Get version from package metadata or pyproject.toml"""
    try:
        # First try to get version from installed package metadata
        return get_package_version("sugarai")
    except Exception:
        pass

    try:
        # Fallback: read from pyproject.toml (for development)
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        return pyproject["project"]["version"]
    except (FileNotFoundError, KeyError, Exception):
        # Final fallback version
        return "3.0.0"


__version__ = _get_version()
__title__ = "Sugar ✨ 🍰 ✨ "
__description__ = "Persistent memory for AI coding agents"
__author__ = "Steven Leggett"
__author_email__ = "contact@roboticforce.io"
__url__ = "https://github.com/roboticforce/sugar"


def get_version_info() -> str:
    """Get formatted version information"""
    import sys

    # Handle Windows encoding limitations
    try:
        # Try to encode with system encoding to verify it works
        output = f"{__title__} {__description__} v{__version__}"
        output.encode(sys.stdout.encoding or "utf-8")
        return output
    except (UnicodeEncodeError, AttributeError):
        # Fallback for Windows terminals that can't handle emojis
        return f"Sugar {__description__} v{__version__}"


def get_full_version_info() -> str:
    """Get detailed version information"""
    import sys

    # Handle Windows encoding limitations
    try:
        title_line = f"{__title__} v{__version__}"
        title_line.encode(sys.stdout.encoding or "utf-8")
        title = __title__
    except (UnicodeEncodeError, AttributeError):
        title = "Sugar"

    return f"""
{title} v{__version__}
{__description__}

Author: {__author__} <{__author_email__}>
Repository: {__url__}
""".strip()
