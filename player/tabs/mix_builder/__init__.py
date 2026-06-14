from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel


class MixBuilderTab(QWidget):
    """Mix Builder tab placeholder — "Coming Soon"."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('MixBuilderTab')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        label = QLabel("Coming Soon™")
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        label.setStyleSheet("padding: 10px 0 0 0;")
        layout.addWidget(label)
        layout.addStretch()
