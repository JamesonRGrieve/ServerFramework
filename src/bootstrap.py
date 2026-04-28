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
  * a future ``python -m serverframework`` entry point can opt in or out
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


def setup_python_path() -> None:
    """Add the framework's ``src/`` directory and its parent to ``sys.path``.

    This mirrors the historical behavior so ``from lib.Logging import logger``
    style imports keep working when the framework is run from a checkout.
    """
    current_file_path = Path(__file__).resolve()
    src_dir = current_file_path.parent
    root_dir = src_dir.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def run_venv_bootstrap() -> bool:
    """Create a ``.venv`` if needed, install deps, and re-exec under it.

    Returns ``True`` on success, ``False`` on failure. May not return at
    all when ``os.execl`` swaps the running interpreter.
    """
    # Defer the logger import: when this runs from a fresh checkout
    # ``loguru`` may not be installed yet. Fall back to ``print`` if so.
    try:
        from lib.Logging import logger  # type: ignore
    except Exception:  # pragma: no cover - bootstrap path
        class _PrintLogger:
            def debug(self, msg, *a, **kw):
                print(msg)

            info = warning = error = debug

        logger = _PrintLogger()  # type: ignore

    current_file_path = Path(__file__).resolve()
    src_dir = current_file_path.parent
    root_dir = src_dir.parent
    venv_dir = root_dir / ".venv"
    pyproject_file = src_dir / "pyproject.toml"
    requirements_file = root_dir / "requirements.txt"

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
                    ["uv", "pip", "install", "-e", str(src_dir)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            else:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", str(src_dir)],
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
