import os
import sys

from kmdr.gui import entry_point as gui_entry_point
from kmdr.main import entry_point as cli_entry_point

os.environ.setdefault("KMDR_GUI_FONT_SIZE", "12")
os.environ.setdefault("KMDR_GUI_SCALE", "1.2")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--kmdr-cli":
        sys.argv.pop(1)
        cli_entry_point()
    else:
        gui_entry_point()
