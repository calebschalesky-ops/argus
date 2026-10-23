import sys
from pathlib import Path

# let the tests import the `argus` package from the project folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
