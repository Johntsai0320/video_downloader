"""Modern Fluent / macOS inspired QSS stylesheet for the PyQt6 desktop app."""

MODERN_STYLE = """
QMainWindow, QWidget#MainContainer {
    background-color: #f1f5f9;
    font-family: "Segoe UI", "Microsoft JhengHei", "PingFang TC", sans-serif;
    color: #1e293b;
}
QFrame.CardFrame {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QLabel {
    font-size: 13px;
    color: #475569;
}
QLabel#HeaderTitle {
    font-size: 22px;
    font-weight: 700;
    color: #0f172a;
}
QLabel#HeaderSubtitle {
    font-size: 12px;
    color: #64748b;
}
QLabel#SectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: #2563eb;
}
QLineEdit {
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    color: #0f172a;
    selection-background-color: #3b82f6;
}
QLineEdit:focus {
    background-color: #ffffff;
    border: 1.5px solid #3b82f6;
}
QLineEdit:disabled {
    background-color: #e2e8f0;
    color: #94a3b8;
}
QComboBox {
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
    color: #0f172a;
}
QComboBox:hover {
    border-color: #94a3b8;
}
QCheckBox {
    font-size: 13px;
    color: #334155;
    font-weight: 500;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #cbd5e1;
    background-color: #f8fafc;
}
QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #2563eb;
}
QPushButton {
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton.SecondaryBtn {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    color: #334155;
}
QPushButton.SecondaryBtn:hover {
    background-color: #f8fafc;
    border-color: #94a3b8;
}
QPushButton.SecondaryBtn:pressed {
    background-color: #e2e8f0;
}
QPushButton.AccentBtn {
    background-color: #0284c7;
    color: #ffffff;
    border: none;
}
QPushButton.AccentBtn:hover {
    background-color: #0369a1;
}
QPushButton#PrimaryActionBtn {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    padding: 10px 24px;
    font-size: 14px;
}
QPushButton#PrimaryActionBtn:hover {
    background-color: #1d4ed8;
}
QPushButton#PrimaryActionBtn:disabled {
    background-color: #94a3b8;
}
QPushButton#CancelActionBtn {
    background-color: #ef4444;
    color: #ffffff;
    border: none;
    padding: 10px 20px;
}
QPushButton#CancelActionBtn:hover {
    background-color: #dc2626;
}
QPushButton#CancelActionBtn:disabled {
    background-color: #fca5a5;
}
QProgressBar {
    background-color: #e2e8f0;
    border-radius: 6px;
    text-align: center;
    height: 12px;
}
QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #06b6d4);
    border-radius: 6px;
}
QTextEdit#TerminalLog {
    background-color: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 10px;
    color: #cbd5e1;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    padding: 8px;
}
"""
