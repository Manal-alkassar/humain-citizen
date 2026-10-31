"""Start HUMAIN CITIZEN locally and open it in your browser.

    python run.py            → http://localhost:8000

You can also simply double-click web/index.html: the agent engine runs in the browser.
"""
import runpy
import sys
from pathlib import Path

sys.argv = [str(Path(__file__).with_name("server.py"))] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
