# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Заводит в целевом репо эфемерный runner scale set и слушает его очередь. Пришёл job — контроллер забирает его и диспатчит `runner.yml` у себя. Тот поднимает внутри `ubuntu-latest`, регистрирует его в scale set целевого репо по JIT-конфигу, отрабатывает один job и умирает. Больше `NESTED_MAX` раннеров одновременно не бывает. `docker compose down` сносит scale set, раннеров и висящие запуски.

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
      # GH_REPO: wprhvso/nested-runner
      # NESTED_SCALE_SET: nested
      # NESTED_MAX: 20
      # NESTED_WORKFLOW: runner.yml
      # NESTED_DEBUG: 1
```

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

## Лимиты GitHub

У токена 5000 REST-запросов в час, и из этого же кармана платят диспатч
раннеров, JIT и уборка. Раньше выбранный лимит валил контроллер: `403` летел
мимо всех обработчиков, `run()` умирал, супервизор перезапускался — а старт
заново проверяет права, ветку и запуски, то есть доедает остаток. Теперь так:

- **Опрос не стоит лимита.** Списки запусков и раннеров опрашиваются условными
  запросами (`If-None-Match`). Ответ `304` GitHub в лимит не засчитывает, а
  разобранный результат лежит в памяти — платим только за изменения.
- **Пустой флот не опрашивается вовсе.** Запуски заводим только мы, так что
  пока ни одного раннера и ни одного диспатча в воздухе нет, спрашивать
  нечего. Холостой контроллер не тратит ничего.
- **Фон живёт на доле остатка.** Интервал фоновой сверки считается из
  `x-ratelimit-remaining` и времени до сброса окна и растягивается сам, когда
  остаток тает. Неприкосновенный запас оставлен диспатчу и уборке; между
  целями доля делится, потому что токен на всех один.
- **Упёршись в лимит, контроллер не разваливается.** `403`/`429` с
  соответствующими заголовками отличается от `403` «нет прав»: первый закрывает
  шлюз до `x-ratelimit-reset` (запросы отсекаются не выходя в сеть), второй
  по-прежнему падает сразу. Сессия очереди при этом не рвётся — она живёт на
  другом хосте и своего лимита не знает, — так что уже взятые job'ы
  доигрываются, а новые раннеры поедут, когда окно откроется.
- **Проверки на старте не повторяются** каждые полминуты: результат живёт
  10 минут, а сами пробы условные.
- **Уборка сначала бесплатная.** Сессия и scale set сносятся через pipeline API,
  у которого своего лимита нет; без scale set живые раннеры job всё равно не
  возьмут. Гашение запусков и удаление раннеров идёт после и пропускается, если
  лимит закрыт: раннеры эфемерные и упрутся в свой `timeout-minutes` сами.

Что видно в логах: `лимит REST закрыт, старт через 300 с — остаток 0, окно ещё
41 мин`. Это не зависание — это ожидание сброса, и оно ограничено окном.

## NixOS

Флейк отдаёт пакет и модуль, так что на NixOS контейнер не нужен — контроллер
живёт обычным systemd-юнитом, `gh` и `age` приезжают вместе с ним.

```nix
{
  inputs.nested-runner.url = "github:wprhvso/nested-runner";

  # ...

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
| `just qa` | yamllint, actionlint, ruff, basedpyright |
| `just keys` | сгенерировать пару ключей, уже сделано |
