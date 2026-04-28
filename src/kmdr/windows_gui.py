import os

from kmdr.gui import entry_point

os.environ.setdefault("KMDR_GUI_FONT_SIZE", "12")
os.environ.setdefault("KMDR_GUI_SCALE", "1.2")

if __name__ == "__main__":
    entry_point()
