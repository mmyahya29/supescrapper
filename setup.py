#!/usr/bin/env python3
"""
Setup script for supescrapper development environment.
Creates virtual environment and installs all dependencies.
"""
import subprocess
import sys
import os
from pathlib import Path


def run(cmd: list[str], cwd: Path = None) -> bool:
    """Run command and return success."""
    try:
        result = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
        print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running {' '.join(cmd)}:")
        print(e.stderr)
        return False


def main():
    project_root = Path(__file__).parent
    venv_path = project_root / ".venv"
    python_exe = venv_path / "Scripts" / "python.exe" if sys.platform == "win32" else venv_path / "bin" / "python"

    print("=" * 60)
    print("supescrapper Development Environment Setup")
    print("=" * 60)

    # 1. Create virtual environment
    if venv_path.exists():
        print(f"\n[1/5] Virtual environment already exists at {venv_path}")
    else:
        print(f"\n[1/5] Creating virtual environment at {venv_path}...")
        if not run([sys.executable, "-m", "venv", str(venv_path)], cwd=project_root):
            return 1
        print("Virtual environment created.")

    # 2. Upgrade pip
    print("\n[2/5] Upgrading pip...")
    if not run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], cwd=project_root):
        return 1

    # 3. Install package in editable mode with all extras
    print("\n[3/5] Installing supescrapper with all extras...")
    if not run([str(python_exe), "-m", "pip", "install", "-e", ".[all]"], cwd=project_root):
        return 1

    # 4. Install Playwright browsers
    print("\n[4/5] Installing Playwright browsers...")
    if not run([str(python_exe), "-m", "playwright", "install", "chromium"], cwd=project_root):
        print("Warning: Playwright install failed. Run manually: python -m playwright install chromium")

    # 5. Install openpyxl for XLSX export
    print("\n[5/5] Installing openpyxl for XLSX export...")
    if not run([str(python_exe), "-m", "pip", "install", "openpyxl"], cwd=project_root):
        return 1

    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print(f"\nTo activate the environment:")
    if sys.platform == "win32":
        print(f"  .venv\\Scripts\\activate")
    else:
        print(f"  source .venv/bin/activate")
    print(f"\nTo run tests:")
    print(f"  python -m pytest -q")
    print(f"\nTo run CLI:")
    print(f"  python -m supescrapper.cli --help")
    print(f"  # or after activation:")
    print(f"  supescrapper --help")

    return 0


if __name__ == "__main__":
    sys.exit(main())