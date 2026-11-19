# Этап 5: наблюдаемость и восстановление

Этап добавляет эксплуатационный профиль поверх завершённого этапа 4. Исходные результаты предыдущих этапов сохраняются. [SLI/SLO, консистентность и RTO/RPO](reliability.md), [runbook](runbooks.md), [конфигурации](../infra/stage5/compose.yaml) и сырые JSON ниже позволяют проверить границы выводов.

## Запуск и доступ

Нужны Docker Compose, Python 3 и свободные loopback-порты. Команды выполняются из корня репозитория. Одновременно запускать старые стенды с теми же портами нельзя. Остановка через `down` сохраняет тома.

```bash
export STAGE5_PROJECT=hdls-stage5-verify
C=(docker compose -p "$STAGE5_PROJECT" -f infra/stage4/compose.yaml -f infra/stage5/compose.yaml)
"${C[@]}" build commerce-1 search provider
python3 scripts/init_stage5.py
"${C[@]}" ps
curl http://localhost:18080/readyz
```

Стенд остановлен с сохранением томов, чтобы не оставлять фоновый рост WAL и нагрузку на Mac. Для повторного запуска достаточно `STAGE5_PROJECT=hdls-stage5-verify python3 scripts/init_stage5.py`, без пересборки существующих локальных образов.

Новый собственный стенд можно создать под другим STAGE5_PROJECT после освобождения портов. Имя `hdls-stage5-verify` обозначает сохранённый проверочный стенд. Первоначальные `hdls-stage4` и `hdls-stage5`, а также recovery volumes сохранены отдельно; команды не удаляют их данные. `migrate` является one-shot job: штатный код выхода 0 означает завершение, не аварию. Миграции и demo-seed исполняются до запуска API с SQL timeout 60 с; рабочий SQL остаётся ограничен 2 с.

| Интерфейс | Адрес / доступ |
| --- | --- |
| Commerce gateway | http://localhost:18080; Basic buyer-N/demo-buyer-N, admin/demo-admin |
| Прямые API | localhost:18081 / 18082 |
| Search / media / provider | localhost:18085 / 18086 / 18087 |
| Grafana dashboard | [Ecom reliability](http://localhost:13000/d/ecom-stage5), admin/local-grafana-password |
| Prometheus / Alertmanager | http://localhost:19090 / http://localhost:19093 |
| Tempo API | http://localhost:13200; Explore datasource в Grafana |
| RabbitMQ UI | http://localhost:15672, commerce/local-rabbit-password |

Management 9091 не опубликован на хост. UI и demo API привязаны к loopback. У Alertmanager только локальный receiver, внешние сообщения не отправляются. [Dashboard](../infra/stage5/grafana/dashboards/ecom.json) содержит 22 панели. Собираются 17 targets: 6 Java-процессов, PostgreSQL, Redis, RabbitMQ, HAProxy, Garage, Tempo, OpenSearch exporter и 4 HTTP probes. ECS JSON logs читаются через Compose logs; Loki не развёрнут.

## Функциональные проверки

```bash
STAGE5_PROJECT=hdls-stage5-verify python3 scripts/check_stage5.py
STAGE5_PROJECT=hdls-stage5-verify python3 scripts/check_stage5_followup.py
docker run --rm --entrypoint=promtool -v "$PWD/infra/stage5:/etc/prometheus:ro" \
  prom/prometheus:v3.5.0 test rules /etc/prometheus/alerts_test.yaml
```

Эти команды намеренно останавливают зависимости выбранного проекта, поэтому запускаются отдельно от штатной нагрузки. Основной отчёт — [stage5_checks.json](stage5_checks.json), исходное измерение сохранено в [stage5_initial_checks.json](stage5_initial_checks.json), уточнения после исправлений — [stage5_followup_results.json](stage5_followup_results.json).

Maven verify прошёл: 34 модульных теста Commerce, 0 failures/errors/skipped (JUnit Jupiter, AssertJ, Mockito). Проверены приватность management, provisioned dashboard, наличие инфраструктурных метрик, настоящий trace HTTP → SQL outbox → RabbitMQ → consume.notifications в Tempo, admission и durable replay, продолжение платежа при блокировке новых checkout, retention published/inbox при сохранении старого unpublished события. Для trace использован явный sampled W3C traceparent; обычная выборка составляет 10%.

Admission локального профиля: 50 000 pending событий или oldest age 600 с. Снимок обновляется до раза в 5 с. Publisher idle interval 200 мс → 2 с, rebuild probe — 5 с. [Idle-измерение](stage5_idle_results.json): 633 SQL-вызова за 30,243 с, то есть 20,93 SQL/с без пользовательского трафика, включая health/exporters/canary/schedulers. Исходная теоретическая оценка publisher + rebuild polling — 50 SQL/с; фактический замер «до» не проводился, поэтому точный коэффициент ускорения не заявляется.

## Отказы и границы восстановления

Во время последовательных отказов шёл отдельный paced поток: 100 catalog + 20 search RPS. Это проверка деградации на чтении, не полный mixed 640 RPS. Создание заказа/платёж/media и корректность проверялись целевыми операциями. Исходный генератор пропустил 207 arrivals при намеренных отказах; статусы/количества доступны в JSON.

| Остановленный компонент | Алерт | От начала stop до алерта, с | От start до готовности/разгрузки, с | Наблюдаемое поведение |
| --- | --- | --- | --- | --- |
| commerce-1 | TargetDown | 33,088 | 21,865 | Второй API работает; 97 catalog 503 в окне обнаружения |
| Redis | RedisUnavailable | 38,150 | 0,704 | Все 3885 catalog и 777 search запросов успешны, bounded SQL fallback |
| RabbitMQ | OutboxLag | 109,151 | 4,640 | Заказ принят, outbox сохранён и разгружен после возврата брокера |
| OpenSearch | EndpointUnavailable | 35,546 | 16,743 | Catalog доступен, Search возвращает ограниченный 503, очередь догоняет |
| PostgreSQL, graceful stop | DatabaseUnavailable | 23,178 | 13,390 | SQL-зависимые операции недоступны, после старта состояние сохранено |
| Garage | TargetDown | 46,244 | 7,791 | Media HIT доступен, MISS 502/504, upload 503 MEDIA_UNAVAILABLE |
| Provider | TargetDown | 42,298 | 22,507 | Инициация сохраняет PENDING, после восстановления сверка возобновляется |

Время start → recovery не включает предшествующий outage и обнаружение. Rabbit alert зависит от создания события и возраста outbox. Выключение одного API даёт окно 503 до health exclusion; непрерывная безошибочность при этом отказе не заявляется.

Отдельный PostgreSQL SIGKILL сохранил подтверждённый PAID-заказ и идемпотентный replay, restart → ready 4,882 с. Проверены fsync=on и synchronous_commit=on. Это crash recovery на сохранённом томе, не потеря диска и не автоматический promotion standby. Нарушений inventory/order/reservation инвариантов после сценариев не обнаружено.

В исходном SQL-fault опыте 24 публичных catalog запроса получили 401: необработанная ошибка соединения попадала в защищённый ERROR dispatch. Это ошибка классификации, которая могла скрыть технический отказ из SLI. Исправлены error dispatch и mapping ошибки SQL, отдельно SQL failure при Basic auth. Начальный JSON сохранён; отдельный followup проверяет 503 на публичных и приватных запросах, 401 для неверного пароля при здоровой БД и сохранность заказа.

В ходе первого прерванного опыта старое правило expiry считало любое пересечение expires_at; при постоянном потоке краткие штатные expiry создавали предупреждение. После опыта gauge уточнён: только просрочка более 10 с. Promtool проверяет pending/firing/resolved, отдельный живой опыт останавливает workers и подтверждает срабатывание/разрешение алерта. Это сигнал backlog; распределение latency всех release не измерено.

## Backup / PITR / S3

```bash
STAGE5_PROJECT=hdls-stage5-verify python3 scripts/restore_stage5.py
```

Скрипт кратко приостанавливает writers для согласования SQL/S3 manifest и создаёт отдельное recovery-окружение. Исходная БД не подменяется. [Результат drill](stage5_restore_results.json):

- pg_basebackup + pg_verifybackup — 3,981 с;
- новый оплаченный заказ создан после base backup и восстановлен из archived WAL;
- именованная точка `ecom_b3278c82c0`, LSN `0/29051C20`; изменение после точки исключено;
- recovery → доступный API — 17,560 с; fingerprints заказа/запаса/платежа совпали;
- 2 объекта Garage, 240606 байт, HTTP GET и SHA-256 совпали;
- восстановление выполнено в отдельном `hdls-recovery-b3278c82c0`, backup и volumes сохранены.

WAL публикуется через temporary file + sync + atomic rename + sync directory. Archive timeout 60 с; volume находится на том же Mac. Полноценная независимая DR-копия, fencing, межзонный failover и внешняя платёжная сверка этим опытом не проверены. Производные Search/Redis восстанавливаются отдельно; полный rebuild из SQL проверен в этапе 4. Restore time относится к малому локальному набору, не к целевому многолетнему объёму.

## Нагрузочная проверка: результаты и отложенный повтор

**Новых прогонов сейчас нет.** Стенд остановлен с сохранением томов, нагрузочная приёмка отложена по просьбе пользователя. [Статус этапа](stage5_status.json) отдельно фиксирует реализацию и незавершённую проверку производительности.

Первый полный час не прошёл пороги: 2 120 936 primary операций, 589,15 фактического RPS при заданных 640, p95 210,51 мс, p99 1021,41 мс, 972 технические ошибки primary, 14 media-ошибок, 9 support-ошибок и 262 320 dropped arrivals всех сценариев. Инварианты, deadlocks и OOM — 0. [Исходный час](stage5_first_hour_results.json) и [его ресурсы](stage5_first_hour_resource_results.json) сохранены. Зелёные up/readiness при этом не означали выполнение клиентского SLO.

CPU k6 во второй части часа приблизился к выделенным двум ядрам одновременно с приближением памяти к Go limit 1 ГиБ. GC pressure является рабочей гипотезой: точного профиля GC в этом опыте не было. На Mac после опыта также зафиксировано около 30 ГиБ занятого swap; вклад хоста отдельно не изолирован. Нельзя приписывать все задержки только backend или только генератору.

### Подготовленные исправления

- Предвыделение уменьшено с 718 до 219 VU: расчёт на 200 мс, параметр VU_PREALLOC_SECONDS. Максимум 1445 VU, частота, веса операций, media-поток и пороги сохранены.
- k6: cgroup 2 ГиБ, Go memory limit 1536 МиБ, CPU 2. Излишне тесный Go limit не должен превращать генератор в bottleneck; эту гипотезу ещё нужно проверить под нагрузкой.
- Каждые 30 с сохраняются клиентский счётчик операций, RPS за интервал, dropped, технические ошибки суммарно и за завершённую минуту, p95 завершённой минуты, cgroup CPU/throttling и память генератора. Отклонение RPS ниже 98% заданного после стартового окна помечается явно. Недостаток клиентских наблюдений не позволяет принять прогон.
- Добавлен ApiLatencyHigh: HTTP p95 Commerce >200 мс или Search >300 мс в течение 2 мин. Это серверная метрика; она дополняет клиентский контроль, не заменяет его.
- По измеренным пикам увеличены cgroup limits Tempo 512 → 768 МиБ, Grafana 256 → 384 МиБ и blackbox 32 → 64 МиБ. Их Go limits: 384/192/32 МиБ соответственно.

Короткая проба с меньшим предвыделением 133 VU (50 мс) завершилась до просьбы отложить прогоны: ошибок 0, p95 2,77 мс, но dropped 977. [Результат пробы](stage5_generator_results.json) сохранён; её пороги также не пройдены. Текущие 219 VU и новый клиентский мониторинг после этой пробы под нагрузкой не запускались. [Настройки генератора](stage5_generator_config.json) проверяются через k6 inspect, который не исполняет HTTP-нагрузку.

### Как повторить позже

Команды ниже предназначены для отдельного согласованного запуска, сейчас они не выполнялись:

```bash
STAGE5_PROJECT=hdls-stage5-verify python3 scripts/init_stage5.py
# Короткая проверка с числом минутных метрик как у часового теста:
METRIC_WINDOWS=60 STAGE5_PROJECT=hdls-stage5-verify caffeinate -i \
  python3 scripts/load_stage5.py --seconds 180 --warmup-seconds 0 \
  --output-name stage5_generator_results.json
# После проверки клиента и ресурсов — отдельный прогрев 180 с и час:
STAGE5_PROJECT=hdls-stage5-verify caffeinate -i \
  python3 scripts/load_stage5.py --seconds 3600
python3 scripts/analyze_stage5_resources.py
python3 scripts/analyze_stage5.py --write
python3 scripts/analyze_stage5.py --check
```

Перед часом проверить фактический RPS, dropped, загрузку CPU, cgroup throttling и память k6, а также давление памяти Mac. Если клиент не выдерживает заданный поток, сначала исправить измерительный стенд. Исходные неуспешные измерения сохраняются; нельзя объявлять достигнутыми 640 RPS только по configuredRate или ослаблять пороги ради приёмки. `analyze_stage5.py --check` проверяет соответствие документации JSON, а `--require-passed` дополнительно требует завершённой нагрузочной приёмки.

### Границы измерения

Профиль повторяет model/capacity.json: catalog/card/search/cart/checkout/history/detail/payment/webhook/admin changes/uploads, плюс media 161 GET/с по 120303 байт. Media GET повторяет один immutable объект через Nginx cache: после первого MISS преобладают HIT. Это не проверка 161 uncached S3 GET/с, широкого медианабора или удалённого CDN/WAN. ImageUpload использует маленький PNG; большие PUT здесь не измеряются.

HTTP latency — k6 response.timings.duration (sending + waiting + receiving), без blocked/DNS/connect/TLS. Запросы идут по локальному HTTP с keep-alive. Полный клиентский SLO через WAN/TLS требует отдельной точки измерения; перцентили фаз нельзя складывать. Demo Basic-аутентификация, компактные payload и синтетические checkout с разными SKU также ограничивают переносимость результата на production.

Дополнительные setup/provider/callback/canary requests считаются отдельно от primary RPS. Включены scrape 10 с, traces 10%, retention, WAL archive и business gauges. Прогрев и штатная нагрузка учитываются отдельно. Минутные latency/error ряды, ресурсы и клиентские наблюдения сохраняются в artifacts/stage5/load и JSON отчёта.

[Последний полный час](stage5_load_results.json), [сводка его ресурсов](stage5_resource_results.json), [ранний smoke без прогрева](stage5_initial_smoke_results.json), [повторный Java smoke](stage5_smoke_results.json) остаются измерениями соответствующих конфигураций. Первый smoke не имел ошибок/dropped, но нарушил imageUpload p95 на трёх загрузках; это не успешная финальная проверка. Compose ps не включает one-shot k6, поэтому его память дополнительно фиксировалась операторскими docker stats snapshots. Это выборочные максимумы; пики между снимками могут быть выше.

Первый 32-минутный опыт прерван после OOM Tempo; [данные](stage5_interrupted_run.json) сохранены. В нём также было 4213 dropped arrivals и превышения минутного p99. Его configured duration 3600 с не является выполненным часом. Исходный 60-секундный прогрев имел 19 технических ошибок и 3 dropped. Все эти результаты отделены от будущей приёмки. Один час сам по себе не доказывает месячный SLO или устойчивость к потере AZ.

<!-- stage5-results:start -->
**Нагрузочная приёмка не завершена. Повторный прогон отложен пользователем; текущая конфигурация генератора под нагрузкой ещё не проверена.**

Реализованы Prometheus/Alertmanager, 22 панели Grafana, 17 scrape targets, Tempo с W3C-контекстом через outbox/RabbitMQ и ECS JSON logs. Admission/retention и 34 модульных теста Commerce проверены; после SIGKILL PostgreSQL подтверждённый PAID и replay сохранены.

| Измерение | Секунды | Фактический primary RPS | HTTP p95 / p99, мс | Технические ошибки | Dropped всех сценариев | Пороги |
| --- | --- | --- | --- | --- | --- | --- |
| Прогрев перед первым часом | 180 | 640.04 | 2.20 / 5.66 | 0 | 0 | пройден |
| Первый полный час | 3600 | 589.15 | 210.51 / 1021.41 | 972 | 262320 | не пройден |
| Проба с предвыделением 50 мс | 180 | 635.37 | 2.77 / 30.28 | 0 | 977 | не пройден |

В первом полном часу выполнено 2,120,936 primary запросов при заданных 640 RPS. Инварианты / deadlocks / OOM — 0, но latency и пропуски нагрузки не прошли пороги. Зелёные up/readiness не подтверждают выполнение клиентского SLO. Выборочный максимум Docker working set с генератором — 5.60 ГиБ; CPU k6 во второй части упирался в выделенные 2 CPU. Связь с ограничением Go memory — рабочая гипотеза, а не доказанный профиль GC.

Исправленная конфигурация: 219 предвыделенных VU, максимум 1445 сохранён, задано 640.02 primary RPS. Предвыделение рассчитано на 200 мс и настраивается через VU_PREALLOC_SECONDS. Лимиты k6 — 2 ГиБ / Go 1536 МиБ; добавлен live-контроль клиентских RPS, dropped, p95 завершённой минуты и cgroup CPU/memory. Лимиты Tempo/Grafana/blackbox увеличены по измеренным пикам. Новый час и финальный smoke ещё предстоят; пороги не ослаблены.

Локальный PITR восстановил оплаченный заказ и 2 изображения с совпадающим SHA-256; изменение после точки исключено. Base backup/verify 3.981 с, recovery → API 17.560 с. Это локальный drill; многозонные RPO/RTO остаются целями архитектуры.

Сохранены первый прерванный опыт с OOM Tempo, неуспешный полный час и короткие пробы. Ошибочные 401 при отказе SQL исправлены и отдельно перепроверены как 503. [Все результаты и ограничения](stage5_verification.md), [SLO](reliability.md), [runbook](runbooks.md).
<!-- stage5-results:end -->

## Что остаётся для целевого развёртывания

Репликация/quorum по независимым узлам, PostgreSQL HA manager и fencing, независимый WAL/S3 backup с retention, узловой мониторинг, внешние alert receivers, защищённые credentials/TLS, реальный provider contract и reconciliation после PITR требуют отдельного окружения. Чекпоинт связывает эти требования с runbook и проверяет доступный локальный сценарий; production RPO 0 / RTO 5 минут остаются целями архитектуры.
