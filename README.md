# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

## Как это работает

Контроллер заводит в целевом репо эфемерный runner scale set и слушает его очередь. Пришёл job — контроллер забирает его и диспатчит `runner.yml` у себя. Тот поднимает раннер внутри `ubuntu-latest`, регистрирует его в scale set целевого репо по JIT-конфигу, отрабатывает один job и умирает. Больше `NESTED_MAX` раннеров одновременно не бывает. `docker compose down` сносит scale set, раннеров и висящие запуски.

## Что нажать

### 1. Создать токен

Аккаунт должен быть админом целевого репозитория и коллабаратором этого.

**Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)**.

Имя `nested`, no expiration, скоупы `repo` и `workflow`.

### 2. Положить compose-файл

`mkdir -p /opt/nested-runner && nano /opt/nested-runner/compose.yml`:

```yml
services:
  nested-runner:
    image: ghcr.io/wprhvso/nested-runner:latest
    container_name: nested-runner
    restart: unless-stopped
    stop_grace_period: 2m
    command: ["owner/target"]
    environment:
      GH_TOKEN: ${GH_TOKEN:?нужен токен в .env}
```

Обязателен только `GH_TOKEN`, остальное — по желанию:

| Переменная | По умолчанию | Что задаёт |
|---|---|---|
| `GH_TOKEN` | — | токен со скоупами `repo` и `workflow` |
| `GH_REPO` | `wprhvso/nested-runner` | репозиторий, где диспатчится `runner.yml` |
| `NESTED_SCALE_SET` | `nested` | имя scale set в целевом репозитории |
| `NESTED_MAX` | `20` | предел раннеров на одну цель |
| `NESTED_WORKFLOW` | `runner.yml` | workflow, поднимающий один раннер |
| `NESTED_DEBUG` | пусто | подробный лог очереди и диспатчей |

Токен — рядом, в `/opt/nested-runner/.env`:

```
GH_TOKEN=ghp_...
```

```bash
chmod 600 /opt/nested-runner/.env
```

Целевых репозиториев можно перечислить сразу несколько: `command: ["owner/a", "owner/b"]`.

### 3. Запустить

```bash
env -C /opt/nested-runner docker compose up -d
```

Логи:

```bash
env -C /opt/nested-runner docker compose logs -f
```

Снести:

```bash
env -C /opt/nested-runner docker compose down
```

### 4. Поправить `runs-on` во всех нужных workflow

```yml
runs-on: nested
```

Использовать массивы в `runs-on` нельзя.

### 5. Проверить запуском своего workflow

## Обновление

```bash
env -C /opt/nested-runner docker compose pull && env -C /opt/nested-runner docker compose up -d
```

## NixOS

Флейк отдаёт пакет и модуль: контроллер живёт systemd-юнитом, `gh` и `age` приезжают вместе с ним.

```nix
{
  inputs.nested-runner.url = "github:wprhvso/nested-runner";

  modules = [ inputs.nested-runner.nixosModules.default ];
}
```

```nix
{
  services.nested-runner = {
    enable = true;
    repos = [ "owner/target" ];
    environmentFiles = [ "/var/lib/secrets/nested-runner" ];
  };
}
```

Токен — в файле из `environmentFiles`, одной строкой `GH_TOKEN=ghp_...`.
Остальное настраивается опциями: `homeRepo`, `scaleSet`, `maxRunners`,
`workflow`, `debug`.

## Команды

| Команда | Что делает |
|---|---|
| `just build` | собрать образ локально |
| `just run <repo>` | собрать и запустить, переменные подставит сам |
| `just version [vX.Y.Z]` | показать или проставить версию |
| `just tag` | тег `vX.Y.Z` и пуш |
| `just test` | тестовый workflow |
| `just qa` | qa-shell и qa-python теми же конфигами, что в CI |
| `just fix` | то же с автоправкой |
| `just keys` | сгенерировать пару ключей, уже сделано |
