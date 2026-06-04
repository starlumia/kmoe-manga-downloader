import os

from kmdr.gui import entry_point as gui_entry_point

os.environ.setdefault("KMDR_GUI_FONT_SIZE", "18")
os.environ.setdefault("KMDR_GUI_SCALE", "1.2")

if __name__ == "__main__":
    gui_entry_point()
