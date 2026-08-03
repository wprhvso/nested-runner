# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Заводит в целевом репо эфемерный runner scale set и слушает его очередь. Пришёл job — контроллер забирает его и диспатчит `runner.yml` у себя. Тот поднимает внутри `ubuntu-latest`, регистрирует его в scale set целевого репо по JIT-конфигу, отрабатывает один job и умирает. Больше `NESTED_MAX` раннеров одновременно не бывает. Ctrl+C сносит scale set, раннеров и висящие запуски.

## Что нажать

### 1. Создать токен

Аккаунт должен быть админом целевого репозитория и коллабаратором домашнего.

**Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)**.

Имя `nested`, no expiration, скоупы `repo` и `workflow`.

### 2. Запустить раннер

```bash
docker run -d --name nested-runner --restart unless-stopped -e GH_TOKEN=ghp_... ghcr.io/wprhvso/nested-runner:0.1.0 owner/target
```

Можно сразу несколько репозиториев указать.

Логи — `docker logs -f nested-runner`, снести — `docker rm -f nested-runner`.

### 3. Поправить `runs-on` во всех нужных workflow

```yml
runs-on: nested
```

Использовать массивы в `runs-on` нельзя.

### 4. Проверить запуском своего workflow

## Настройки

Переменными окружения:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `GH_TOKEN` | — | токен GitHub, обязателен |
| `GH_REPO` | `wprhvso/nested-runner` | домашний репозиторий `owner/name` |
| `NESTED_SCALE_SET` | `nested` | имя scale set в целевом репо, оно же `runs-on` |
| `NESTED_MAX` | `10` | потолок одновременных раннеров |
| `NESTED_WORKFLOW` | `runner.yml` | workflow, который поднимает раннер |
| `NESTED_DEBUG` | — | подробные логи |

```bash
docker run --rm -it -e NESTED_MAX=3 ... ghcr.io/wprhvso/nested-runner owner/target
```

## Команды

| Команда | Что делает |
|---|---|
| `docker run ... <repo>` | запустить цикл для целевого репо |
| `just build` | собрать образ локально |
| `just run <repo>` | собрать и запустить, переменные подставит сам |
| `just version [vX.Y.Z]` | показать или проставить версию |
| `just tag` | тег `vX.Y.Z` и пуш |
| `just test` | тестовый workflow |
| `just qa` | yamllint, actionlint, ruff, basedpyright |
| `just keys` | сгенерировать пару ключей, уже сделано |
