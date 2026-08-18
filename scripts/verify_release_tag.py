"""Verify that a release tag matches the package version before publishing."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


def package_version(pyproject: Path) -> str:
    with pyproject.open("rb") as source:
        value = tomllib.load(source)["project"]["version"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("project.version must be a non-empty string")
    return value.strip()


def validate_release_tag(tag: str, version: str) -> None:
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"release tag `{tag}` does not match package version `{expected}`")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args()
    version = package_version(args.pyproject)
    validate_release_tag(args.tag, version)
    print(f"release tag {args.tag} matches package version {version}")


if __name__ == "__main__":
    main()
