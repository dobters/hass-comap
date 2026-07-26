#!/usr/bin/env python3
"""Sync manifest.json, hacs.json, and .python-version from pyproject.toml + uv.lock."""

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"
MANIFEST = ROOT / "custom_components" / "comap" / "manifest.json"
HACS = ROOT / "hacs.json"
PYTHON_VERSION = ROOT / ".python-version"


def _pinned_versions(lock_text: str) -> dict[str, str]:
    """Return {package_name: version} for all packages in uv.lock."""
    pinned: dict[str, str] = {}
    for block in re.split(r"\n\[\[", lock_text):
        name_m = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        ver_m = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if name_m and ver_m:
            pinned[name_m.group(1)] = ver_m.group(1)
    return pinned


def _package_name(dep: str) -> str:
    """Extract bare package name from a PEP 508 dependency string."""
    return re.split(r"[>=<!;\[]", dep)[0].strip().lower().replace("_", "-")


def _min_version(dep: str) -> str | None:
    """Extract the minimum version from a PEP 508 constraint like 'pkg>=1.2.3'."""
    m = re.search(r">=\s*([^\s,;]+)", dep)
    return m.group(1) if m else None


def main() -> int:
    pyproject = tomllib.loads(PYPROJECT.read_text())
    version = pyproject["project"]["version"]
    deps: list[str] = pyproject["project"].get("dependencies", [])
    dev_deps: list[str] = pyproject.get("dependency-groups", {}).get("dev", [])

    pinned = _pinned_versions(LOCK.read_text())
    changed = False

    # --- manifest.json: version + requirements ---
    requirements: list[str] = []
    missing: list[str] = []
    for dep in deps:
        name = _package_name(dep)
        if name not in pinned:
            missing.append(name)
        else:
            requirements.append(f"{name}=={pinned[name]}")

    if missing:
        print(f"error: packages not found in uv.lock: {missing}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("version") != version or manifest.get("requirements") != requirements:
        manifest["version"] = version
        manifest["requirements"] = requirements
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"updated manifest.json: version={version} requirements={requirements}")
        changed = True

    # --- hacs.json: homeassistant min version ---
    ha_dep = next((d for d in dev_deps if isinstance(d, str) and _package_name(d) == "homeassistant"), None)
    if ha_dep is None:
        print("error: homeassistant not found in dev dependencies", file=sys.stderr)
        return 1
    ha_min = _min_version(ha_dep)
    if ha_min is None:
        print(f"error: no >= constraint found on homeassistant dep: {ha_dep!r}", file=sys.stderr)
        return 1

    hacs = json.loads(HACS.read_text())
    if hacs.get("homeassistant") != ha_min:
        hacs["homeassistant"] = ha_min
        HACS.write_text(json.dumps(hacs, indent=2) + "\n")
        print(f"updated hacs.json: homeassistant={ha_min}")
        changed = True

    # --- .python-version: major.minor from requires-python ---
    requires_python: str = pyproject["project"].get("requires-python", "")
    py_min = _min_version(requires_python)
    if py_min is None:
        print(f"error: no >= constraint in requires-python: {requires_python!r}", file=sys.stderr)
        return 1
    # Keep only major.minor so uv/pyenv picks the latest patch.
    py_minor = ".".join(py_min.split(".")[:2])

    current_py = PYTHON_VERSION.read_text().strip()
    if current_py != py_minor:
        PYTHON_VERSION.write_text(py_minor + "\n")
        print(f"updated .python-version: {current_py} -> {py_minor}")
        changed = True

    return 1 if changed else 0  # non-zero signals pre-commit that files were modified


if __name__ == "__main__":
    sys.exit(main())