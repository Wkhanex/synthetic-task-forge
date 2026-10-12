"""Run the repository CLI without requiring an editable installation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from task_forge.cli import main
raise SystemExit(main())
