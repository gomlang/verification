import argparse
from pathlib import Path
import re

from ecosystem import inventory


TEMPLATE = """name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  verify:
    uses: @USES@
    with:
      module: @MODULE@
      verification-ref: @REF@
"""


def workflow(module, revision):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", module):
        raise ValueError("invalid module name")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("shared workflow requires a full commit SHA")
    local = module == "verification"
    uses = "./.github/workflows/ecosystem-ci.yml" if local else f"gomlang/verification/.github/workflows/ecosystem-ci.yml@{revision}"
    ref = "${{ github.sha }}" if local else revision
    return TEMPLATE.replace("@USES@", uses).replace("@MODULE@", module).replace("@REF@", ref)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--libraries", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    differences = []
    for module in sorted(set(inventory()) | {"verification"}):
        root = args.libraries / module
        if not (root / ".git").exists():
            raise ValueError(f"repository is missing: {module}")
        destination = root / ".github/workflows/ci.yml"
        expected = workflow(module, args.revision)
        if args.check:
            if not destination.is_file() or destination.read_text() != expected:
                differences.append(module)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(expected)
    if differences:
        parser.exit(1, "CI workflow drift: " + ", ".join(differences) + "\n")


if __name__ == "__main__":
    main()
