"""Standalone venv + dependency bootstrap for ``python app.py``.

Historically the venv-creation and ``pip install -e .`` logic lived at the
top of ``app.py`` and was guarded by ``if __name__ == "__main__"``. That
worked but coupled "library use" and "first-run setup" in the same file:
importing ``app`` for its ``instance`` factory still pulled in subprocess,
shutil, venv, etc.

This module isolates that bootstrap so:

  * importing ``app`` never executes any subprocess / pip work.
  * ``python app.py`` keeps doing exactly what it did before — it now just
    calls into here.
  * a future ``python -m zephyrex`` entry point can opt in or out
    of the bootstrap explicitly.

Anything imported from inside the framework should be deferred (function-
local imports), because the whole point of running this script is that
the framework's own dependencies may not be installed yet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

# The checkout layout: ``<root>/src/zephyrex/bootstrap.py``. ``pyproject.toml``,
# ``requirements.txt`` and ``.venv`` live at ``<root>``.
_PACKAGE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _PACKAGE_DIR.parent
_ROOT_DIR = _SRC_DIR.parent


def setup_python_path() -> None:
    """Put the directory holding the ``zephyrex`` package (``src/`` in a
    checkout) on ``sys.path``.

    The package directory itself is deliberately not added: every framework
    import is rooted at ``zephyrex.``, and exposing its subpackages as
    top-level ``extensions``, ``lib``, ``logic`` or ``database`` would shadow
    a consumer's own packages of those names.
    """
    if str(_SRC_DIR) not in sys.path:
        sys.path.insert(0, str(_SRC_DIR))


def run_venv_bootstrap() -> bool:
    """Create a ``.venv`` if needed, install deps, and re-exec under it.

    Returns ``True`` on success, ``False`` on failure. May not return at
    all when ``os.execl`` swaps the running interpreter.
    """
    # Defer the logger import: when this runs from a fresh checkout
    # ``loguru`` may not be installed yet. Fall back to ``print`` if so.
    try:
        from zephyrex.lib.Logging import logger
    except Exception:  # pragma: no cover - bootstrap path
        from logging import getLogger

        logger = getLogger(__name__)

    venv_dir = _ROOT_DIR / ".venv"
    pyproject_file = _ROOT_DIR / "pyproject.toml"
    requirements_file = _ROOT_DIR / "requirements.txt"

    use_uv = shutil.which("uv") is not None
    use_conda = shutil.which("conda") is not None

    # If we're not in a virtual environment, create one and restart.
    if sys.prefix == sys.base_prefix:
        if not venv_dir.exists():
            logger.debug(f"Creating virtual environment at {venv_dir}")
            try:
                if use_uv:
                    logger.debug("Using uv for faster virtual environment creation...")
                    subprocess.run(["uv", "venv", str(venv_dir)], check=True)
                elif use_conda:
                    logger.debug("Using conda for virtual environment creation...")
                    subprocess.run(
                        ["conda", "create", "-p", str(venv_dir), "python", "-y"],
                        check=True,
                    )
                else:
                    venv.create(venv_dir, with_pip=True)
            except Exception as e:
                logger.debug(f"Failed to create virtual environment: {e}")
                return False

        python_path = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
        logger.debug(f"Restarting with virtual environment at {python_path}...")
        try:
            os.execl(str(python_path), str(python_path), *sys.argv)
        except Exception as e:
            logger.debug(f"Failed to restart with virtual environment: {e}")
            return False

    logger.debug("Running in virtual environment, checking requirements...")

    if pyproject_file.exists():
        logger.debug(f"pyproject.toml found at {pyproject_file}, installing...")
        try:
            if use_uv:
                logger.debug("Using uv for faster package installation...")
                subprocess.run(
                    ["uv", "pip", "install", "-e", str(_ROOT_DIR)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            else:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", str(_ROOT_DIR)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            logger.info("Dependencies from pyproject.toml installed successfully!")
        except subprocess.CalledProcessError as e:
            logger.debug(f"Failed to install from pyproject.toml: {e.stderr}")
            return False
        except Exception as e:
            logger.debug(f"Error installing from pyproject.toml: {e}")
            return False
    elif requirements_file.exists():
        logger.debug("Requirements file found, installing...")
        try:
            if use_uv:
                logger.debug("Using uv for faster package installation...")
                subprocess.run(
                    ["uv", "pip", "install", "-r", str(requirements_file)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            else:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        str(requirements_file),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            logger.info("Requirements.txt installed successfully!")
        except subprocess.CalledProcessError as e:
            logger.debug(f"Failed to install requirements.txt: {e.stderr}")
            return False
        except Exception as e:
            logger.debug(f"Error installing requirements.txt: {e}")
            return False
    else:
        logger.debug(
            f"No pyproject.toml at {pyproject_file} or "
            f"requirements.txt at {requirements_file}"
        )

    logger.debug("Requirements complete, configuring PATH...")
    setup_python_path()
    logger.info("PATH configuration complete, local modules loadable.")
    return True


# Backwards-compatible alias for the historical ``_venv`` symbol.
_venv = run_venv_bootstrap
