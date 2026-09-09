"""Launch the standalone own-game diagnostic from any working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from career_session_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
