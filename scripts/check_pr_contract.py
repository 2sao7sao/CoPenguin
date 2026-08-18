"""Fail CI when a pull request silently creates another long-lived branch stack."""

from __future__ import annotations

import argparse


def validate_pr_base(*, base: str, default: str, labels: set[str]) -> str:
    base = base.strip()
    default = default.strip()
    normalized_labels = {label.strip().lower() for label in labels if label.strip()}
    if not base or not default:
        raise ValueError("base and default branch names are required")
    if base == default:
        return f"pull request targets the default branch `{default}`"
    if "stacked-pr" in normalized_labels:
        return f"stacked pull request targets `{base}` with explicit `stacked-pr` label"
    raise ValueError(
        f"pull request targets `{base}` instead of `{default}`; rebase onto `{default}` "
        "or add the `stacked-pr` label and name the parent PR"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--default", required=True)
    parser.add_argument("--labels", default="")
    args = parser.parse_args()
    labels = set(args.labels.split(","))
    print(validate_pr_base(base=args.base, default=args.default, labels=labels))


if __name__ == "__main__":
    main()
