"""Run from a checkout without pip install or third-party packages."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from dtf_test_gen.cli import main
if __name__ == "__main__":
    raise SystemExit(main())
