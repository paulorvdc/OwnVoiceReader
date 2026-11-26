"""Minimal shim. All project metadata lives in pyproject.toml (A11).

This file exists so legacy tooling that still calls `setup.py` (and so the
project can be `pip install -e .`'d without a build isolation step) finds a
setup.py. Distribution / packaging (PyInstaller/Briefcase, M6) is deferred.
"""

from setuptools import setup

# Everything (name, version, deps, entry points, package-data) is declared in
# pyproject.toml; this is intentionally a no-op so we don't duplicate metadata.
setup()
