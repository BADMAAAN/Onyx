"""Explicit own-window fixture for literal input and semantic UIA events.

No external messages/actions. Only the button writes this fixture's JSON file.
Importing the module opens nothing; the coordinator starts it for live checks.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton
    app = QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle('Onyx Interaction Fixture')
    window.resize(720, 420)
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel('Локальная тестовая заметка'))
    editor = QPlainTextEdit()
    editor.setAccessibleName('Тестовая заметка')
    editor.setObjectName('fixture_note')
    editor.setPlaceholderText('Точный буквальный ввод без отправки')
    layout.addWidget(editor)
    button = QPushButton('Подтвердить тестовую заметку')
    button.setAccessibleName('Подтвердить тестовую заметку')
    button.setObjectName('fixture_confirm')
    layout.addWidget(button)
    status = QLabel('Ожидает подтверждения')
    status.setAccessibleName('Статус тестовой заметки')
    layout.addWidget(status)
    events = []
    def confirm():
        event = {'timestamp': datetime.now(timezone.utc).isoformat(), 'text': editor.toPlainText(),
                 'event': 'fixture_note_confirmed', 'source': 'own_fixture_button'}
        events.append(event)
        path = ROOT/'data'/'integration'/'interaction_fixture.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'events': events[-20:]}, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        status.setText(f'Заметка подтверждена. Подтверждений: {len(events)}')
    button.clicked.connect(confirm)
    window.show()
    QTimer.singleShot(30*60*1000, app.quit)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
