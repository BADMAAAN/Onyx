"""Read-aloud prompts; no expected result is substituted for real capture."""
GROUPS = {
'ru': [
'Кей Кей, открой блокнот на втором экране.', 'Покажи, какие окна открыты на первом мониторе.',
'Перенеси это окно на второй экран.', 'Верни меня в предыдущее приложение.',
'Что означает ошибка в текущем окне?', 'Прочитай текст в активном окне.',
'Сделай громкость немного тише.', 'Поставь громкость пятьдесят, нет, тридцать.',
'Открой браузер, хотя нет, сначала блокнот.', 'Напиши Ивану, нет, ничего не пиши.',
'Найди папку с проектом агента.', 'Покажи последние изменённые файлы.',
'Создай текстовый файл в папке проекта.', 'Скопируй этот файл в новую папку.',
'Переименуй файл, но не удаляй его.', 'Прочитай первые строки файла.',
'Почему программа завершилась с ошибкой?', 'Объясни этот код простыми словами.',
'Запусти тесты и скажи, прошли ли они.', 'Покажи состояние репозитория.',
'Приостанови музыку на время разговора.', 'Включи обратно тот же проигрыватель.',
'Что сейчас играет в браузере?', 'Переключи на следующий трек.',
'Верни предыдущую песню.', 'Открой настройки звука.',
'На каком экране находится мессенджер?', 'Подожди, я ещё не закончил.',
'Кей Кей, открой, хотя нет, ничего не открывай.', 'Спасибо, на этом закончим.'
],
'en': [
'KK, open Telegram on the second monitor.', 'Open the current project in the editor.',
'Move this window to the first screen.', 'What is on both monitors right now?',
'Go back to the previous application.', 'Read the error in the active window.',
'Explain why this test failed.', 'Show the status of this repository.',
'Create a new text file in this folder.', 'Copy the file without replacing the original.',
'Open Chrome, no, open Firefox.', 'Set the volume to fifty, actually thirty.',
'Do not send that message.', 'Pause the music.', 'Resume the same player.',
'Skip to the next track.', 'What song is playing?', 'Open sound settings.',
'Wait, I have not finished yet.', 'Stop talking and listen to me.'
],
'mixed': [
'Открой VS Code и запусти build.', 'Открой Settings and check Bluetooth.',
'Кей Кей, покажи git status проекта.', 'Запусти Python tests в этой папке.',
'Перенеси Telegram на second monitor.', 'Открой README and explain the setup.',
'Почему PowerShell возвращает access denied?', 'Проверь Ollama и loaded models.',
'Открой Codex в project KK.', 'Не запускай deploy, только build.',
'Покажи GitHub pull request в браузере.', 'Поставь музыку на pause.',
'Resume музыку из того же player.', 'Запусти test suite и прочитай output.',
'Открой terminal в папке проекта.', 'Что означает connection refused в этом log?',
'Покажи memory usage процесса Python.', 'Открой Visual Studio, no, VS Code.',
'Скопируй config file в новую папку.', 'Проверь Focusrite input device.'
],
'technical': [
'Проверь Ollama.', 'Какая модель Qwen сейчас загружена?', 'Используй Qwen3-VL для изображения.',
'Открой VS Code.', 'Открой Visual Studio.', 'Покажи PowerShell output.',
'Передай эту задачу Codex.', 'Открой AyuGram.', 'Найди Telegram на втором экране.',
'Проверь Focusrite.', 'Покажи GitHub.', 'Выполни Git status.',
'Какая версия Python установлена?', 'Открой SpaceGram.', 'Объясни настройки FLUX.',
'Покажи Qwen3.', 'Проверь CUDA и VRAM.', 'Почему faster-whisper работает на CPU?',
'Что означает int8_float16?', 'Прочитай pyproject.toml.'
],
'short': ['Стоп.', 'Остановись.', 'Хватит.', 'Отмена.', 'Stop.', 'Cancel.',
'Открой Telegram.', 'Открой блокнот.', 'Следующий трек.', 'Предыдущий трек.',
'Выключи музыку.', 'Включи обратно.', 'Тише.', 'Громче.', 'На второй экран.',
'Вернись назад.', 'Продолжай.', 'Подожди.', 'Что это?', 'Кей Кей.']
}

def corpus():
    return [dict(id=f'{group}_{i:02}',group=group,expected=text)
            for group,phrases in GROUPS.items() for i,text in enumerate(phrases,1)]
