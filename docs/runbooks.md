# Runbook учебного магазина

Команды выполняются из корня репозитория. Для оставленного проверочного стенда используйте `STAGE5_PROJECT=hdls-stage5-verify`; для своего нового стенда можно выбрать другое имя. Ниже `C` — Compose выбранного проекта:

```bash
export STAGE5_PROJECT=hdls-stage5-verify
C=(docker compose -p "$STAGE5_PROJECT" -f infra/stage4/compose.yaml -f infra/stage5/compose.yaml)
"${C[@]}" ps
```

Этот Bash/Zsh-массив не меняет другие Compose-проекты. Обычная остановка — `"${C[@]}" down` с сохранением томов. Удаление томов не является способом исправления инцидента. Восстановление проводится в отдельном проекте. Исходный `hdls-stage4` и его данные сохранены отдельно.

Начальные точки: [Grafana](http://localhost:13000/d/ecom-stage5), [Prometheus](http://localhost:19090), [Alertmanager](http://localhost:19093), RabbitMQ UI localhost:15672, бизнес-состояние `GET /api/admin/background-stats` с ADMIN. Grafana: admin / local-grafana-password; Rabbit: commerce / local-rabbit-password. Это loopback/demo credentials.

<a id="api"></a>
## API: ошибки или задержки

1. Сопоставить фактически доставленный клиентский RPS, dropped/ошибки генератора и gateway response counters, HTTP histograms приложения, backend queue, Hikari pending, CPU/RSS/GC. Проверить, какие маршруты и экземпляры затронуты. Не смешивать health probes, бизнес-409 и технические 5xx.
2. Проверить `/readyz` через 18080 и напрямую 18081/18082, затем SQL/Redis. Убедиться, что HAProxy исключает нездоровый сервер; для оперативного вывода можно использовать Runtime API по инструкции этапа 3.
3. Восстанавливать/перезапускать по одному API, сохраняя работающий экземпляр. Дождаться readiness и slowstart 60 с. Увеличивать число соединений БД без проверки насыщения нельзя.
4. Потерянный ответ checkout повторять с тем же Idempotency-Key и тем же телом. Другой ключ создаёт другую операцию. Проверить единственный order/reservation, статус и инвариант запаса.
5. Сохранить интервал инцидента, traceId/корреляционный ID, метрики и фактический пользовательский эффект. Код 503 OUTBOX_CAPACITY разбирать по разделу broker; MEDIA_UNAVAILABLE — по media.

<a id="dependency"></a>
## Компонент недоступен или метрики устарели

Проверить `up`, `probe_success`, `pg_up`, `redis_up`, `opensearch_up`, время последнего бизнес-снимка и статус контейнера. `up=1` у exporter не означает доступность его хранилища. Старые gauges не считаются свежими после last_success timeout. Для logs:

```bash
"${C[@]}" logs --since 15m --tail 200 commerce-1 commerce-2 worker-1 worker-2
```

В ECS JSON искать traceId/spanId и business trace_id; затем открыть trace в Grafana Explore/Tempo. Наличие correlation ID не гарантирует, что trace был выбран сэмплированием. В production нужны отдельные доступы к логам и контроль чувствительных полей.

<a id="database"></a>
## PostgreSQL

1. Проверить pg_up, соединения относительно max_connections=80, Hikari waiters, блокировки, дисковое место и ошибки WAL archive. Сначала найти причину, не сбрасывать данные/том.
2. При остановке процесса на сохранённом томе запустить только PostgreSQL, дождаться завершения WAL recovery и SQL-readiness, затем проверить API. Зафиксировать время рестарта отдельно от полного outage/detection.
3. На реальном HA-кластере до promotion исключить двойную primary: fencing старой primary, quorum HA manager, пригодный синхронный standby и его LSN. Автоматического HA/fencing на локальном одноузловом стенде нет.
4. После восстановления проверить подтверждённый заказ, durable replay, локальный платёж, остатки и резервы; убедиться, что outbox догнал брокер и сверка продолжилась. Сверить состояние провайдера при неоднозначных исходах.
5. При повреждении/потере данных использовать restore/PITR в новом окружении. Не поднимать повреждённую старую primary в режим записи рядом с восстановленной.

<a id="cache"></a>
## Redis

Проверить redis_up, hit ratio, ошибки клиента, SQL pool и CATALOG_BUSY. Каталог использует bounded fallback: четыре origin-load на API и ожидание до 500 мс в полном стенде. Корзина и заказ остаются в PostgreSQL. Восстановить Redis и наблюдать прогрев/SQL; не увеличивать fallback concurrency ради устранения 503 без оценки БД. Потеря содержимого кэша допустима, очищать PostgreSQL при этом не нужно.

<a id="broker"></a>
## Брокер и outbox

1. Проверить RabbitMQ/quorum, oldest outbox age, backlog, free disk и publisher failures. Outbox подтверждает фиксацию бизнес-события в SQL, queue — последующую доставку.
2. При 25 000 событий или 300 с возраста вмешаться до admission threshold. На 50 000 / 600 с новые checkout могут получать OUTBOX_CAPACITY. Оставить свободное место на ранее принятые операции, оплату, refund и release.
3. Восстановить брокер/маршрутизацию. Проверить persistent queues, bindings и publisher confirms; после lease до 30 с возможны повторные публикации.
4. Наблюдать уменьшение outbox и очередей, не только факт подключения. Admission снимается после свежего SQL-снимка, обычно до 5 с. Не выставлять published_at вручную и не удалять unpublished rows ради зелёного dashboard.
5. Если диск близок к пределу, ограничить вход раньше. Параметры OUTBOX_MAX_PENDING/OUTBOX_MAX_AGE_SECONDS меняются согласованно с запасом диска и скоростью критических событий; сами по себе они не являются жёсткой дисковой квотой.

<a id="worker"></a>
## Worker, expiry и накопление задач

Проверить оба процесса, scheduling failures, Hikari, очередь и возраст событий. Истечение и сверка имеют отдельные scheduler tasks, поэтому отказ поиска не должен остановить освобождение запаса. После перезапуска дождаться истечения leases и сравнить ACTIVE reservations с inventory.reserved. Заказ PAID не должен освобождаться expiry. Большую догоняющую обработку ограничивать batch/prefetch, чтобы не вытеснить пользовательские запросы из SQL/CPU.

<a id="dlq"></a>
## DLQ

1. Выяснить eventId/schema_version, queue, trace_id и причину. Сначала исправить зависимость или consumer.
2. Повторить ограниченную пачку через ADMIN:

```bash
curl -u admin:demo-admin -X POST \
  'http://localhost:18080/api/admin/queues/search/replay?limit=100'
```

3. Для notifications использовать соответствующее имя очереди. Replay сохраняет eventId и подтверждает новую публикацию до ack из DLQ. После исправления poison event нельзя бесконечно возвращать без изменения причины.
4. Проверить inbox, версии OpenSearch и единственность внешнего эффекта. Для сообщений старше retention inbox подтвердить idempotency horizon внешнего получателя. Не переписывать eventId для обхода dedup.

<a id="search"></a>
## Поиск и свежесть

Проверить Search probe, OpenSearch health/heap/FS, queue/DLQ и canary last_success/latency histogram. При отказе поиск возвращает 503; категории и checkout должны работать из Commerce/SQL. Восстановить OpenSearch, вернуть исправленные задания из DLQ, затем проверить новые версии через Search API. При потере индекса запустить rebuild:

```bash
curl -u admin:demo-admin -X POST http://localhost:18080/api/admin/search/rebuilds
curl -u admin:demo-admin http://localhost:18080/api/admin/search/rebuilds
```

Дождаться DONE и проверить alias/count/filter/версию. Rebuild регистрирует BUILDING до scan и пишет с внешними версиями. Не заменять проверку остатка при checkout поисковым документом. Старый индекс удаляется только после grace и проверки отсутствия alias; последние два RETIRED сохраняются.

<a id="payment"></a>
## Платёж / возврат задерживается

Проверить PENDING/REFUND_PENDING, oldest age, attempts/last_error, провайдера и callback delivery. HTTP timeout не означает отсутствие списания. Не создавать новую локальную paymentId и не менять SUCCEEDED на PENDING вручную. Восстановить доступ, позволить worker запросить авторитетный snapshot. Поздний успех по EXPIRED должен завершиться REFUNDED, а запас остаться освобождённым. При неизвестном внешнем состоянии требуется сверка с провайдером, не повторное списание по новому ключу.

У учебного провайдера неоплаченная сессия закрывается после 30 минут. Реальный провайдер должен подтвердить контракт идемпотентности, сроки сессии/ключа, lookup/refund и подпись callback; локальный stub не доказывает его SLA.

<a id="inventory"></a>
## Нарушен инвариант

Приостановить новые checkout/admission и изменения запасов; сохранить SQL snapshot, события и trace IDs. Сверить inventory.reserved с суммой ACTIVE reservation_items, PAID с CONFIRMED, отсутствие отрицательного/превышенного запаса. Не исправлять reserved произвольным UPDATE без проверки реальных оплат/заказов и журнала. Для учебного стенда воспроизвести сценарий в отдельном проекте и устранить причину, затем выполнить регрессию. Для production нужен согласованный процесс коррекции и аудит финансового эффекта.

<a id="media"></a>
## Garage / изображения

Проверить Garage metrics/health, S3 PUT/GET/HEAD и медиаконтур отдельно. Cache HIT может возвращаться при недоступном origin, поэтому он не доказывает здоровье Garage. Неуспешная загрузка оставляет PENDING intent, не READY metadata; через час cleanup удаляет сироту и повторяет DELETE. Immutable READY объекты восстанавливаются по SQL manifest и SHA-256. При срочном удалении публичного файла учитывать уже закэшированную копию; приватные документы через этот cache не обслуживаются.

<a id="backup"></a>
## Бэкап и PITR

1. Проверить pg_stat_archiver failures/last archived WAL, наличие проверенного base backup, непрерывность цепочки и копию объектов. Не полагаться только на сообщение «backup complete»: выполнить pg_verifybackup и restore drill.
2. Выбрать точку восстановления и сформировать новое окружение. Пример учебного drill:

```bash
STAGE5_PROJECT=hdls-stage5-verify python3 scripts/restore_stage5.py
```

3. Скрипт кратко останавливает writers выбранного проекта для согласованного SQL/S3 manifest. Перед запуском понимать это окно недоступности. Бэкап и восстановленные тома сохраняются для разбора. Оригинальная БД не заменяется восстановленной.
4. Проверить именованную точку, исключение изменений после неё, fingerprints заказа/запаса/платежей, HTTP ownership/status и checksum изображений. Кэш заполняется заново, поиск перестраивается из SQL по отдельному сценарию.
5. В production дополнительно нужны независимая копия вне отказавшего узла/региона, доступы/ключи, DNS/сеть/секреты, fencing старой системы, reconciliation с платёжным провайдером и репетиция переключения трафика. Время локального drill не включает эти этапы.

<a id="disk"></a>
## Диск и рост WAL

Проверить свободное место Docker VM и физического Mac, WAL/archive growth, replication slots и очереди. Убрать некритическую входную нагрузку, сохранить резерв для WAL. Не удалять активный pg_wal и не чистить архив по одному возрасту. Сначала обеспечить проверенный base backup и весь требуемый PITR horizon в независимом хранилище. Если копирование не успевает, рассчитать время до заполнения и принять явное решение об ограничении записи. В локальном стенде несжатый archive_timeout 60 с может расходовать около 1 ГиБ/час даже при малом трафике.

После любого инцидента сохранить симптомы/таймлайн, различить факт, предположение и измерение, проверить бизнес-инварианты и обновить тест/runbook. Ошибка мониторинга не должна автоматически считаться здоровьем сервиса.

## Контроль измерительного стенда

При нагрузочной проверке up/readiness недостаточны. В artifacts/stage5/soak-live.json проверять client_interval_rps, dropped_iterations, technical_errors, p95 завершённой минуты и generator_interval_cpu_percent. Сохранять cgroup cpu.stat (особенно nr_throttled/throttled_usec) и memory.current. При ограничении генератора нельзя приписывать его задержки только backend. На Mac дополнительно контролировать memory_pressure, vm_stat и sysctl vm.swapusage; большой занятый swap сам по себе не доказывает активную подкачку — нужны дельты.
