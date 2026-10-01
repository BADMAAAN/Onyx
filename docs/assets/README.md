# Публичные изображения / Public images

- `onyx-logo.png`: точная копия `onyx_desktop/assets/pearl.png`, используемого текущими `main.py` и `qml/Pearl.qml`. Без нового дизайна или изменения пропорций.
- `onyx-interface.png`: новый снимок настоящего QML-клиента на 2026-10-01, `--visual-test --visual-size 1280x800 --smoke-seconds 4`. Режим не создаёт backend worker, не читает preferences/историю, не запускает аудио. Пустая история; модельный сеанс не имитируется. Сцена использует английский язык по умолчанию в visual mode.
- Снимок захвачен встроенным `QQuickWindow.grabWindow`, без рабочего стола и заголовка ОС. Видимые строки проверены вручную: только стандартные элементы интерфейса. Личные чаты не удалялись. Исходный снимок сохранён вне репозитория; публичная копия имеет те же пиксели и lossless PNG-оптимизацию.

Повторить в корне репозитория (путь снимка задайте вне пользовательских данных):

```powershell
.\run-app.ps1 --visual-test --visual-size 1280x800 --smoke-seconds 4 --screenshot <output.png>
```

Команда создаёт также диагностический JSON с локальными путями: **не публикуйте его**. Проверьте все видимые строки перед добавлением PNG.

English: the logo is an exact copy of the current canonical Pearl. The interface is a fresh capture of the real QML scene in isolated visual mode, not a live inference session. No user history, preferences or audio were accessed; no chats were deleted. Only standard UI strings are visible. PNG optimization is lossless. The capture's diagnostic JSON is private and must not be published.
