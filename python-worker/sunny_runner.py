from __future__ import annotations

import sys

from sunny_core.worker import run_sunny_task

__all__ = ["run_sunny_task"]


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: python -m sunny_runner <task_id>", file=sys.stderr)
        return 2
    run_sunny_task(sys.argv[1].strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
