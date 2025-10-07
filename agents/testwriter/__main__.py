"""Module entry point for `python -m agents.testwriter`."""

from .agent import main

if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
