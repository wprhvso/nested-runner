# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Два репозитория. **Контроллерный** — этот, в нём лежит `runner.yml` и секрет `RUNNER_PAT`; из его каталога запускается `just run`. **Целевой** — тот, где крутятся ваши workflow с `runs-on: nested`, он передаётся аргументом.

Контроллер заводит в целевом репо эфемерный runner scale set и слушает его очередь. Пришёл job — контроллер забирает его и диспатчит `runner.yml` у себя. Тот поднимает внутри `ubuntu-latest` настоящий раннер, регистрирует его в scale set целевого репо по JIT-конфигу, отрабатывает один job и умирает. Больше `NESTED_MAX` раннеров одновременно не бывает. Ctrl+C сносит scale set, раннеров и висящие запуски.

## Что нажать

### 1. Залогинить gh

```bash
gh auth login
```

Аккаунт должен быть админом целевого репозитория.

### 2. Положить RUNNER_PAT

Fine-grained PAT **на целевой** репозиторий: Administration rw, Contents r. Кладётся в этот репо:

```bash
just secret ghp_xxx
```

### 3. Запустить

```bash
just run owner/target
```

Тестовый workflow (`test.yml` должен лежать в целевом репо):

```bash
just test owner/target
```

## Настройки

Переменными окружения:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `NESTED_SCALE_SET` | `nested` | имя scale set в целевом репо, оно же `runs-on` |
| `NESTED_MAX` | `10` | потолок одновременных раннеров |
| `NESTED_WORKFLOW` | `runner.yml` | workflow, который поднимает раннер |
| `NESTED_DEBUG` | — | подробные логи |

```bash
NESTED_MAX=3 just run owner/target
```

## Команды

| Команда | Что делает |
|---|---|
| `just run <repo>` | запустить цикл для целевого репо |
| `just secret <token>` | положить RUNNER_PAT в текущий репо |
| `just test <repo>` | тестовый workflow |
| `just qa` | yamllint, actionlint, ruff, basedpyright |
