"""Example helper script bundled inside sample-skill (never executed by ContextBridge)."""

from __future__ import annotations

import platform
import sys


def main() -> None:
    print(f"Python {sys.version.split()[0]} on {platform.system()}")


if __name__ == "__main__":
    main()
