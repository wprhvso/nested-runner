# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Контроллер заводит в репо эфемерный runner scale set и слушает его очередь. Пришёл job для `runs-on: nested` — контроллер забирает его и диспатчит `runner.yml`. Тот поднимает внутри `ubuntu-latest` настоящий раннер по JIT-конфигу, отрабатывает один job и умирает. Больше `NESTED_MAX` раннеров одновременно не бывает. Ctrl+C сносит scale set, раннеров и висящие запуски.

## Что нажать

### 1. Залогинить gh

```bash
gh auth login
```

### 2. Запустить

```bash
just run owner/name
```

Тестовый workflow:

```bash
just test owner/name
```

## Настройки

Переменными окружения:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `NESTED_SCALE_SET` | `nested` | имя scale set, оно же `runs-on` |
| `NESTED_MAX` | `10` | потолок одновременных раннеров |
| `NESTED_WORKFLOW` | `runner.yml` | workflow, который поднимает раннер |
| `NESTED_DEBUG` | — | подробные логи |

```bash
NESTED_MAX=3 just run owner/name
```

## Команды

| Команда | Что делает |
|---|---|
| `just run <repo>` | запустить цикл |
| `just test <repo>` | тестовый workflow |
| `just qa` | yamllint, actionlint, ruff, basedpyright |
