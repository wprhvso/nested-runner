# nested-runner

Self-hosted раннеры GitHub Actions, которые сами крутятся внутри GitHub Actions (`ubuntu-latest`). Матрёшка.

Зачем: доказать, что так можно. Кто потащит в прод — тот лох.

## Как это работает

Локальный контроллер раз в 10 секунд смотрит, сколько у репо свободных раннеров. Если меньше нужного — запускает `runner.yml`, который поднимает ещё один эфемерный раннер.

## Что нажать

### 1. Сделать свой репозиторий

Это твоя матрёшка, а не моя — секреты и раннеры будут жить в твоём репо.

```bash
gh repo fork wprhvso/nested-runner --clone --fork-name nested-runner
cd nested-runner
```

Или проще: кнопка **Use this template** → `gh repo clone owner/name`.

Дальше везде `owner/name` — это твой свежесозданный репо.

### 2. Включить Actions в форке

В форках workflow'ы выключены по умолчанию. Открыть вкладку **Actions** и нажать зелёную кнопку согласия. Один раз.

### 3. Сделать PAT

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained.

Права на свой репо:

- **Actions** — Read and write
- **Administration** — Read and write (без этого не увидит список раннеров)
- **Contents** — Read

### 4. Положить PAT в секреты своего репо

```bash
gh secret set RUNNER_PAT --repo owner/name
```

Да, тот же токен. Локально — для контроллера, в секретах — чтобы раннер сам себя зарегистрировал.

### 5. Залогиниться

```bash
just login
```

Спросит токен, проверит доступ, сохранит в `~/.config/nested-runner/token`.

### 6. Заполнить конфиг

Первый запуск создаст `~/.config/nested-runner/config.toml` и вежливо сообщит, что репозиториев там нет. Открыть, дописать свой slug:

```toml
poll_seconds = 10

[[repos]]
slug = "owner/name"
warm = 2
```

`warm` — сколько свободных раннеров держать наготове.

### 7. Запустить

```bash
just run
```

Через минуту в Settings → Actions → Runners появятся раннеры. Проверить:

```bash
just status
gh workflow run test.yml --repo owner/name && gh run watch --repo owner/name
```

`test.yml` выполнится на матрёшечном раннере. Если в логе видно `Runner name: run-...` — работает.

## Команды

| Команда | Что делает |
|---|---|
| `just login` | сохранить токен |
| `just status` | показать, что контроллер видит прямо сейчас |
| `just run` | запустить цикл |
| `just run --once` | один тик и выход |
| `just qa` | форматтер, линтер, тайпчекер |
| `just fix` | починить то, что чинится само |
| `just install` | поставить бинарник `nested-runner` глобально |
| `just lock` | обновить зависимости |
| `just clean` | снести venv и кэши |

Нужны `uv`, `just` и `gh`.

## Когда что-то не так

Контроллер говорит человеческим языком: что сломалось и что делать. Если он молчит, а раннеры не появляются — смотри логи `runner.yml` в Actions.

`just status` показывает `online / idle / inflight / need` — обычно этого хватает, чтобы понять, на каком шаге всё встало.
