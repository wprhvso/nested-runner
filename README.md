# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Локальный контроллер раз в `poll` секунд смотрит, сколько у репо свободных раннеров. Если меньше `warm` — запускает `runner.yml`, который поднимает ещё один эфемерный раннер. Общее число раннеров не переваливает за `max`. Ctrl+C гасит всё за собой сам.

## Что нажать

### 1. Сделать свой репозиторий

Это твоя матрёшка, а не моя — секреты и раннеры будут жить в твоём репо.

```bash
gh repo fork wprhvso/nested-runner --clone --fork-name nested-runner
cd nested-runner
```

Или проще: кнопка **Use this template** → `gh repo clone owner/name`.

### 2. Включить Actions в форке

В форках workflow'ы выключены по умолчанию. Открыть вкладку **Actions** и нажать зелёную кнопку согласия. Один раз.

### 3. Залогинить gh

```bash
gh auth login
```

Контроллер ходит в API через `gh` и оттуда же берёт токен для секрета репо.

### 4. Запустить

```bash
just run owner/name 2 10 10
```

Позиционно: репо, `warm`, `max`, `poll`.

Тестовый action:

```bash
just test owner/name
```

## Команды

| Команда | Что делает |
|---|---|
| `just run <repo> <warm> <max> <poll>` | запустить цикл (Ctrl+C = stop) |
| `just stop <repo>` | отменить все run'ы и снести всех раннеров |
| `just test <repo>` | проверочный workflow |
| `just qa` | yaml и workflow |
