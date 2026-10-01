# Участие в Onyx / Contributing

Onyx в активной разработке. Основной README — русский; английская версия должна содержать те же факты.

## Ошибки и предложения

Создайте issue в существующем репозитории: ожидаемое и фактическое поведение, шаги воспроизведения, commit, Windows/Python и релевантная конфигурация модели или аудио. Не прикладывайте ключи, личные чаты, базы, полные логи или скриншоты без проверки приватности.

## Изменения

Обсудите большие изменения заранее. Делайте небольшие тематические PR, следуйте стилю соседнего кода, объясняйте поведение и проверки. Не смешивайте исправление документации с рефакторингом. Добавляйте содержательные тесты при изменении поведения; для документации проверяйте ссылки, соответствие исходникам и обе языковые версии.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Acceptance с backend требует отдельного изолированного root; не используйте пользовательскую базу. Визуальные снимки делайте через `--visual-test`. Не коммитьте `.env`, runtime control-файлы, историю, веса, окружения или личные данные. Сохраняйте сторонние notices. Корневая лицензия пока не выбрана; не добавляйте лицензию от имени владельца.

## English

Report reproducible issues with expected/actual behavior, revision, Windows/Python and relevant model/audio configuration. Remove secrets and personal content from evidence. Discuss major changes first; submit small focused PRs following nearby code style, explain behavior and validation, and keep both READMEs aligned. Run relevant unittest checks; documentation changes need source/link/privacy checks. Backend acceptance must use an isolated root, never a user database. Use `--visual-test` for screenshots. Preserve third-party notices and do not select an owner license on their behalf.
