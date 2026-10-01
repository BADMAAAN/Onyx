# Запуск и восстановление Windows / Windows setup and recovery

Это публичная инструкция без личных путей и сохранённого состояния разработчика. Команды выполняются из корня проекта; установки на чистой Windows в этой публикационной задаче не проверялись.

## Окружение

Используйте Python 3.12 и локальное окружение `.venv`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt --extra-index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip check
```

Снимок зависимостей ориентирован на Windows; это не переносимый hash lock. Настройте `config.toml` под свои провайдеры, модели и аудио. Текущий профиль выбирает CUDA STT; при отсутствии совместимого оборудования нужна адаптация. Языковые модели и STT/TTS-веса подготавливаются отдельно. Для веб-поиска есть `setup-web.ps1`.

## Запуск и диагностика

```powershell
.\run.ps1 --doctor
.\run-app.ps1
.\run.ps1 --status
```

Клиент может запустить backend самостоятельно. Для явного фонового запуска:

```powershell
.\run.ps1 --background
```

При проблемах сначала проверьте выбранный backend root, окружение, доступность Ollama, наличие моделей и audio devices. Не завершайте все Python-процессы и не удаляйте `data` или preferences ради восстановления окна.

## Данные и резервные копии

История и память backend находятся в локальной `data`; предпочтения клиента используют пользовательский профиль. Control metadata содержит временный credential — не публикуйте его, базы и диагностические логи.

Перед ручным восстановлением остановите соответствующие процессы и сохраните свои базы/настройки. Для существующего локального проекта предусмотрен `scripts/backup_local_state.py`; проверьте его параметры и область действия перед использованием. Не перезаписывайте работающую базу и не восстанавливайте чужие данные. Старые GUI bundles можно сохранить для отката клиента; это не откат базы.

English: use Python 3.12, the repository-local environment and configuration suited to your hardware. Prepare models/voice weights separately. Diagnose the selected backend, model service and audio before changing files. Keep user databases/preferences private, back them up before recovery, and never overwrite a running database. GUI rollback and database restore are separate operations.
