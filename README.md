# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Заводит в целевом репо эфемерный runner scale set и слушает его очередь. Пришёл job — контроллер забирает его и диспатчит `runner.yml` у себя. Тот поднимает внутри `ubuntu-latest`, регистрирует его в scale set целевого репо по JIT-конфигу, отрабатывает один job и умирает. Больше `NESTED_MAX` раннеров одновременно не бывает. Ctrl+C сносит scale set, раннеров и висящие запуски.

## Что нажать

### 1. Залогинить gh

```bash
gh auth login
```

Аккаунт должен быть админом целевого репозитория и коллабаратором этого.

### 2. Запустить

```bash
git clone https://github.com/wprhvso/nested-runner && cd nested-runner
```
```bash
just run owner/target
```

### 3. Поправить `runs-on` во всех нужных workflow

```yml
runs-on: nested
```

Использовать массивы в `runs-on` нельзя.

### 4. Запустить workflow

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
