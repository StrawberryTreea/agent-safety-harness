"""Shared entry point; native executors and scoring contracts remain authoritative."""
from scripts.pipeline_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
