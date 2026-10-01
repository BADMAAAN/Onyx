# Onyx Desktop

Публичный development snapshot / Public development snapshot.

Клиент `onyx_desktop` использует PySide6/Qt Quick/QML и отдельный локальный backend. Запуск из корня проекта:

```powershell
.\run-app.ps1
```

`run-app.ps1` использует `.venv/Scripts/python.exe` и передаёт корень backend. Прямой запуск допускает `--backend-root <folder>`. Клиент не загружает модельные веса; рабочий backend и модели нужны отдельно.

Интерфейс: Apps, Chat, Settings, беседы/проекты, выбор модели, вложения, Dictation и Live. Каталог Apps не означает подключение всех перечисленных внешних инструментов. Возможности и ограничения описаны в основном [README](../README.md).

## Bundle

```powershell
.\build-app.ps1
.\install-client.ps1 -Bundle <path-to-Onyx.dist>
```

Сборка использует отдельное `.venv-app-build`, Nuitka и не менее 8 GiB доступной RAM. Сохраняйте весь bundle, не переносите один EXE. Установщик проверяет manifest/hashes и запускает изолированный smoke-test до смены ярлыка. Backend и веса не входят в standalone GUI.

## Public screenshot

```powershell
.\run-app.ps1 --visual-test --visual-size 1280x800 --smoke-seconds 4 --screenshot <output.png>
```

Visual mode uses the actual QML interface without backend, preferences/history access or audio. It is not a live inference session. Inspect screenshot strings before publishing; do not publish the diagnostic JSON. See [asset provenance](assets/README.md).

English: launch from the repository root with `run-app.ps1` after configuring the backend environment/models. The standalone GUI bundle requires its complete folder and a separately available backend. Build/install scripts verify resources and a smoke launch; they do not imply a stable public release.
