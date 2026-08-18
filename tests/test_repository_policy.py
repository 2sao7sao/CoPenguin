from __future__ import annotations

import pytest

from scripts.check_pr_contract import validate_pr_base
from scripts.verify_release_tag import package_version, validate_release_tag


def test_pr_contract_accepts_default_branch() -> None:
    assert "default branch" in validate_pr_base(base="main", default="main", labels=set())


def test_pr_contract_requires_explicit_label_for_stack() -> None:
    with pytest.raises(ValueError, match="stacked-pr"):
        validate_pr_base(base="feature/parent", default="main", labels=set())

    assert "explicit" in validate_pr_base(
        base="feature/parent",
        default="main",
        labels={"stacked-pr"},
    )


def test_release_tag_matches_package_version(tmp_path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    assert package_version(pyproject) == "1.2.3"
    validate_release_tag("v1.2.3", "1.2.3")
    with pytest.raises(ValueError, match="does not match"):
        validate_release_tag("v1.2.2", "1.2.3")
