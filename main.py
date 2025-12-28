"""
My Singing Monsters Animation Viewer
Main entry point for the application

A comprehensive animation viewer with OpenGL rendering, timeline scrubbing, and export features.
"""

import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import MSMAnimationViewer


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = MSMAnimationViewer()
    window.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
