#!/usr/bin/env python3
"""Run a unittest suite and expose failures as GitHub annotations."""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def annotation_path(test: unittest.case.TestCase) -> str:
    module = sys.modules.get(test.__class__.__module__)
    source = Path(getattr(module, "__file__", ""))
    try:
        return source.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return source.name or "tests"


def escape_annotation(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("start_dir")
    parser.add_argument("--pattern", default="test*.py")
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.discover(args.start_dir, pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for test, detail in [*result.failures, *result.errors]:
            title = escape_annotation(str(test))
            message = escape_annotation(detail[-6000:])
            print(f"::error file={annotation_path(test)},title={title}::{message}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
