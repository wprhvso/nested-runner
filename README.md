# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Локальный контроллер раз в `poll` секунд смотрит, сколько у репо свободных раннеров. Если меньше `warm` — запускает `runner.yml`, который поднимает ещё один эфемерный раннер. Общее число раннеров не переваливает за `max`. Ctrl+C гасит всё за собой сам.

## Что нажать

### 1. Залогинить gh

```bash
gh auth login
```

### 2. Запустить

```bash
just run owner/name 2 10 10
```

Позиционно: `repo`, `warm`, `max`, `poll`.

Тестовый action:

```bash
just test owner/name
```

## Команды

| Команда | Что делает |
|---|---|
| `just run <repo> <warm> <max> <poll>` | запустить цикл |
| `just stop <repo>` | снести всех раннеров |
| `just test <repo>` | тестовый workflow |
| `just qa` | qa: yaml и workflow |
