from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt, QObject, QEvent, QTimer


class MainPanel(QWidget):
    """Central tab-host area: tab-bar header above the tab content stack."""

    def __init__(self, window):
        super().__init__()
        self.setObjectName('MainPanel')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right_panel = QVBoxLayout(self)
        right_panel.setContentsMargins(0, 0, 0, 0)
        right_panel.setSpacing(0)

        window.main_header = QWidget()
        window.main_header.setObjectName('MainHeader')
        window.main_header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        window.main_header.setFixedHeight(62)
        window.main_header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _mh_layout = QHBoxLayout(window.main_header)
        _mh_layout.setContentsMargins(0, 0, 0, 0)
        _mh_layout.setSpacing(0)

        _mh_layout.addStretch()
        _mh_layout.addWidget(window.tab_bar)
        _mh_layout.addStretch()

        # Nav buttons overlay the left panel header's right corner
        window._left_panel.add_header_widget(window.btn_back)
        window._left_panel.add_header_widget(window.btn_fwd)

        # Trigger tab mode check whenever the header is resized (e.g. panels dragged)
        class _HRF(QObject):
            def eventFilter(self_, obj, ev):
                if ev.type() == QEvent.Type.Resize:
                    QTimer.singleShot(0, window._update_tab_mode)
                return False
        window._header_resize_filter = _HRF(window.main_header)
        window.main_header.installEventFilter(window._header_resize_filter)

        right_panel.addWidget(window.main_header)       # tab bar only — own background
        right_panel.addWidget(window.tab_stack, 1)      # content — browser backgrounds only
