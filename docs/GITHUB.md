# Как выложить проект на GitHub

## 1. Аккаунт

Создай аккаунт на [github.com](https://github.com), если его ещё нет.

## 2. GitHub Desktop

Для первого раза проще установить **GitHub Desktop** с [desktop.github.com](https://desktop.github.com/). Он сам проводит вход и умеет загружать папку без командной строки.

1. Открой GitHub Desktop.
2. Войди в свой аккаунт.
3. `File` → `Add local repository`.
4. Выбери папку `wdh-ru-translation`.
5. Если GitHub Desktop предложит создать репозиторий — согласись, назови его `wdh-ru-translation`.
6. Нажми `Publish repository`, оставь **Public**.

## 3. Первый коммит

В GitHub Desktop:

1. Проверь список файлов — не должно быть игры, `_internal`, `dist`, `build`, `*.zip`, бэкапов.
2. В поле сообщения напиши `Initial translation patch`.
3. Нажми `Commit to main`.
4. Нажми `Push origin`.

## 4. Релиз

1. Открой репозиторий на github.com.
2. Перейди в `Releases` → `Draft a new release`.
3. Тег: `v1.1.0`.
4. Заголовок: `v1.1.0`.
5. Прикрепи `WDH_RU_Patcher_v1.1.0.zip` из папки `_release`.
6. Опубликуй.

Архив для релиза собирается по [BUILD.md](BUILD.md). В репозиторий его коммитить не нужно.

## 5. Оформление

- В `About` репозитория добавь описание: `Russian translation patch for With the Devilish Her`.
- Добавь темы: `translation`, `russian`, `unity`, `mod`.
- Включи `Issues`, чтобы люди могли сообщать об ошибках.
- Закрепи релиз и добавь ссылку на него в README.

## 6. Если через командную строку

```powershell
git init -b main
git add .
git commit -m "Initial translation patch"
git remote add origin https://github.com/ТВОЙ_ЛОГИН/wdh-ru-translation.git
git push -u origin main
```

Для HTTPS GitHub попросит не пароль, а Personal Access Token. Если не хочешь с этим возиться — используй GitHub Desktop.

## 7. Важно

- Не загружай игру, `resources.assets.resS` целиком, `_internal` и локальные бэкапы.
- Не называй перевод официальным.
- Предупреждение Windows о неподписанном `.exe` останется, пока не будет подписи кода.
