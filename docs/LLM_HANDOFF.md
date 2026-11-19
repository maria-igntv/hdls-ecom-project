# Передача проекта следующей LLM

Этот документ позволяет продолжить работу без истории переписки. Он описывает задачу, принятые решения, код, результаты, ограничения и порядок следующих действий. Базовый коммит реализации — **7937be1** (`feat: add stage 5 reliability with load acceptance deferred`). Сам документ передачи добавлен после него. При дальнейших изменениях проверять актуальные Git status, код и JSON, а не считать этот снимок неизменным источником истины.

## 1. Самое важное перед любыми действиями

**Пользователь явно отложил нагрузочные прогоны:** «сейчас пока не запускай прогон, сделай где необходимо исправления, а прогон сделаем позже».

Поэтому сейчас разрешены изучение, правки, статические проверки и подготовка. **Не запускать k6-нагрузку, длительные тесты, fault injection или restore drill без нового явного указания пользователя.** Просьба прочитать этот handoff или общее «продолжи» не отменяет ограничение автоматически. Новый прогон текущей конфигурации пока не выполнен. Если позже пользователь разрешит весь цикл короткой и часовой проверки, отдельное повторное подтверждение на каждый уже разрешённый шаг не требуется.

Текущее состояние:

- Этапы 0–4 реализованы; основной код этапа 5 реализован.
- **Нагрузочная приёмка этапа 5 не завершена.** Последний полный час не прошёл пороги.
- Генератор и наблюдаемость исправлены после неуспешных измерений, но текущие настройки под нагрузкой ещё не проверены.
- Проверочный Docker-проект `hdls-stage5-verify` остановлен. Его тома сохранены. Запущенных контейнеров этого проекта при подготовке handoff не обнаружено.
- Исходники, сводки и документация находятся на GitHub. Большие локальные артефакты, Docker volumes и материалы курса туда не входят.
- Не объявлять достигнутыми 640 RPS по configuredRate или зелёным up/readiness: нужен фактически доставленный клиентом поток и успешные пороги.

## 2. Репозиторий и локальное окружение

| Что | Где |
| --- | --- |
| GitHub | https://github.com/maria-igntv/hdls-ecom-project |
| Ветка | `main` |
| Базовый коммит реализации | https://github.com/maria-igntv/hdls-ecom-project/commit/7937be1 |
| GitHub-каталог документов | https://github.com/maria-igntv/hdls-ecom-project/tree/main/docs |
| Этот документ, raw | https://raw.githubusercontent.com/maria-igntv/hdls-ecom-project/main/docs/LLM_HANDOFF.md |
| Главная записка | https://github.com/maria-igntv/hdls-ecom-project/blob/main/README.md |
| План проекта | https://github.com/maria-igntv/hdls-ecom-project/blob/main/project_plan.md |
| Локальная папка | `/Users/maria.ignatova/mipt-hw/hlds` |

Внимание к написанию: GitHub-репозиторий называется **hdls-ecom-project**, локальная папка — **hlds**. Это отдельный Git-репозиторий. Исходный cwd приложения может быть `/Users/maria.ignatova/mipt-hw/lm-hw`, но работать нужно в `hlds`, не в соседнем домашнем задании.

```bash
cd /Users/maria.ignatova/mipt-hw/hlds
git status --short
git log -5 --oneline
```

В новом окружении можно клонировать публичный репозиторий обычным `git clone`. Клонирование не восстановит локальные Docker volumes, изображения Garage, base backup, WAL-архив и сырые результаты из artifacts.

Окружение проведённых опытов: Mac M4 Pro, ARM64, 24 ГиБ физической памяти; Docker VM примерно 7,75 ГиБ / 12 CPU. Docker quotas отдельных контейнеров задаются Compose. Пользователь также располагает ВМ без доступа в интернет и потребительским Облаком Mail на 100 ГБ. Для текущего этапа выбран локальный запуск на Mac. Перенос на ВМ не выполнен и не является текущей задачей. Нельзя считать Облако Mail S3 API или CDN. При будущем переносе проверять архитектуру CPU и доступность образов; ARM64-образы не становятся AMD64-образами после docker save/load.

## 3. Задача курса и желаемый конечный результат

Пользователь проходит курс по распределённым высоконагруженным системам. Тема проекта — электронная коммерция. Основной результат задания — расчётно-пояснительная записка в README публичного репозитория: требования, нагрузка, архитектура, данные, ресурсы, взаимодействия, надёжность, ограничения и эволюция. Работающий прототип дополняет записку проверками; он не заменяет обоснования и расчёты.

Локальные исходные материалы курса:

- `/Users/maria.ignatova/mipt-hw/hlds/lectures/lectures_description.docx` — программа занятий;
- `/Users/maria.ignatova/mipt-hw/hlds/lectures/assignment_checkpoints.docx` — описание задания и чекпоинтов;
- PDF-лекции в том же каталоге.

Каталог lectures исключён из Git. В GitHub есть подготовленный по этим материалам [project_plan.md](../project_plan.md). Балльная рубрика ЛМС отдельно не получена; нельзя обещать конкретную оценку курса.

Ближайший outcome после нового разрешения на нагрузку: проверить исправленный измерительный стенд, завершить честную нагрузочную приёмку этапа 5 либо точно локализовать оставшийся предел, сохранить воспроизводимые результаты и синхронизировать README/JSON. Далее — финальная архитектурная проверка, рост/стоимость и подготовка защиты по project_plan.md.

## 4. Предпочтения пользователя и правила работы

Прочитать [AGENTS.md](../AGENTS.md). Его основные требования:

- Backend — **Java**, решение уже согласовано; не переводить проект на Go.
- Сохранять границы Commerce API, Search API и фоновых обработчиков.
- В Markdown **не добавлять дату создания/фиксации документа**. Даты источников или измерительных периодов допустимы, когда нужны по смыслу.
- Основные результаты чекпоинта помещать в README, детали связывать ссылками.
- Разделять требования, факты, допущения, измерения и гипотезы.
- Сохранять пользовательские изменения и ограничивать правки текущей задачей.

Не удалять Docker volumes для «чистого» запуска без отдельного явного основания. Удаление томов первоначального стенда ранее не было выполнено; вместо этого использовали отдельный проверочный проект. Не останавливать чужие `botify-*` и `redis-container`, не закрывать приложения пользователя ради освобождения памяти самостоятельно. Учитывать ограничения среды на Docker, сеть и Git-запись; не обходить отказы approval-механизма.

## 5. Продукт, нагрузка и инварианты

Создаётся собственный учебный магазин товаров для дома и электроники: **один продавец, единый логический склад, Россия**. Существующие магазины — аналоги для исследования, а не исходный код или система для копирования.

Пять функций MVP: каталог с изображениями; поиск с фильтрами; корзина; создание/резервирование/оплата заказа; история и статус собственных заказов. Административные операции обслуживают эти функции. Нет marketplace, нескольких складов, доставки, рекомендаций, отзывов, промокодов и обычных покупательских возвратов. Компенсационный возврат поздней оплаты входит в MVP.

Основная модель: 1 млн MAU, 200 тыс. DAU, 2 млн зарегистрированных пользователей, 100 тыс. SKU. Модельный API: 64,01 среднего / 640,11 пикового RPS. Пик медиа через CDN — 3226,85 GET/с; расчётный origin при CDN hit 95% — 161,34 GET/с. Это **допущения модели**, не доказанная нагрузочная ёмкость прототипа. Источник чисел — [model/capacity.json](../model/capacity.json), расчёты — [docs/capacity.md](capacity.md).

Инварианты, которые нельзя нарушать при оптимизации:

1. `0 <= inventory.reserved <= inventory.on_hand`; reserved равен сумме ACTIVE reservation_items.
2. Позиции заказа резервируются атомарно: все либо ни одной.
3. Корзина не резервирует товар и не фиксирует цену покупки.
4. Checkout проверяет актуальную цену/наличие в PostgreSQL, сохраняет снимки позиций.
5. Повтор checkout с тем же ключом и телом возвращает исходный результат; другое тело конфликтует. Созданный заказ имеет durable UNIQUE(user,key), независимо от очистки быстрого idempotency cache.
6. Оплата и expiry не могут дважды распорядиться запасом. PAID соответствует CONFIRMED; EXPIRED не возвращается в PAID после поздней оплаты.
7. Неопределённый ответ провайдера требует reconciliation. Поздний успех после expiry компенсируется refund.
8. PostgreSQL — источник истины; Redis и OpenSearch — производные данные. Поиск не подтверждает запас при checkout.
9. Outbox фиксируется в бизнес-транзакции; publisher отмечает published после broker confirm. Доставка at-least-once, повторы допустимы и требуют идемпотентного эффекта.

## 6. Архитектура и карта исходников

Backend: Java 25, Spring Boot 3.5.16, Spring MVC/JDBC/Security, HikariCP, Flyway. Три Maven-проекта, без обязательного локального Maven/JDK: Dockerfiles собирают через Maven/JDK и выполняют verify.

| Путь в GitHub | Назначение |
| --- | --- |
| [backend/commerce](../backend/commerce) | Модульное ядро и тот же JAR в роли worker |
| [backend/search](../backend/search) | Search API → OpenSearch |
| [backend/provider](../backend/provider) | Устойчивый учебный эмулятор оплаты и notification sink |
| [infra/stage4/compose.yaml](../infra/stage4/compose.yaml) | Базовый полный стек |
| [infra/stage5/compose.yaml](../infra/stage5/compose.yaml) | Overlay наблюдаемости/эксплуатации; используется вместе со stage4 |
| [docs/openapi.json](openapi.json) | OpenAPI 3.1, текущая версия описания 0.5.0 |
| [docs/events.schema.json](events.schema.json) | Envelope бизнес-событий |
| [docs/interactions.md](interactions.md) | Протоколы, таймауты, retry, payment/expiry/outbox/DLQ |
| [docs/storage.md](storage.md) | ER, индексы, модели доступа, консистентность |
| [docs/reliability.md](reliability.md) | SLI/SLO, retention/admission, RTO/RPO, backup |
| [docs/runbooks.md](runbooks.md) | Разбор отказов и восстановление |

Commerce-классы лежат в `backend/commerce/src/main/java/ru/mipt/ecom/commerce/`:

- CommerceService / CommerceController — каталог, корзина, checkout, история, административные изменения;
- ProductCache — Redis cache-aside, single-flight и ограниченный SQL fallback;
- ReservationService — settle/expiry; блокировки товаров/остатков в согласованном порядке;
- PaymentService / ProviderClient / PaymentController — локальные состояния, подпись webhook, snapshot/reconcile/refund;
- DomainEvents / BrokerGateway / Workers — outbox, AMQP confirms, consumer inbox, retry/DLQ;
- SearchProjection — versioned OpenSearch projection, rebuild и переключение alias;
- MediaService — S3 upload intents, READY metadata, cleanup сирот;
- BusinessMetrics / EventTracing / FreshnessCanary / Maintenance / OutboxAdmission — основные добавления этапа 5;
- ProbeController / StartupWarmup / SecurityConfig — readiness, прогрев и авторизация.

Инфраструктура: HAProxy, PostgreSQL 17, Redis 7.4, RabbitMQ 4.2 quorum queues, OpenSearch 3.3.2, Garage 2.1 S3, Nginx media cache, k6 1.4.2. Версии смотреть в Compose, эффективные настройки — после merge двух Compose-файлов.

Платёжный provider **не является реальным эквайрингом**. Его схема находится в той же PostgreSQL, что важно для интерпретации PITR. Для настоящего провайдера понадобятся независимый ledger и reconciliation после восстановления.

## 7. Что сделано на этапах 0–4

| Этап | Сделано | Где подтверждение |
| --- | --- | --- |
| 0 | Границы MVP, исследование аналогов/источники, допущения, план, модель нагрузки и расчётная записка | README, product_scope.md, assumptions.md, market_research.md, sources.md, capacity.md |
| 1 | Сетевой путь и балансировка; функциональный Java smoke, health/readiness/drain | balancing.md, stage1_verification.md, infra/lb-smoke |
| 2 | Транзакционное ядро, схема/индексы/SQL-планы, авторизация, конкурентный checkout, идемпотентность, кэш, expiry, restore | stage2_verification.md, stage2_query_plans.json, check_stage2.py |
| 3 | Java/JVM и ресурсные бюджеты, k6, JFR, SQL/память/GC; затем согласованный runtime, холодный запуск, slowstart и rolling | runtime_resources.md, stage3_verification.md, stage3_followup.md и JSON |
| 4 | Оплата/компенсации, outbox/Rabbit/inbox/DLQ, Search/rebuild, Garage/media, OpenAPI/events и интеграционные отказы | stage4_verification.md, stage4_checks.json, stage4_load_results.json |

Исторические результаты нельзя переносить на новый состав стенда автоматически:

- Доработка этапа 3: по 1800 с на 521 и 640 RPS на тогдашнем Commerce-профиле. 640-профиль прошёл без ошибок/dropped; в 521-профиле было 10 технических ошибок в пределах заданного порога. Это не час полного stage5-стека.
- Этап 4: полный mixed 300 с, 192016 primary операций, около 640,05 RPS, p95 1,93 мс / p99 3,68 мс, ошибок/dropped/OOM/инвариантных нарушений 0. Перед ним отдельный 60-секундный прогрев пустого Redis. Media 161 GET/с отдельно. Это короткое успешное измерение, не замена часовому stage5 soak.
- Коммиты: `12b4817` — этап 2; `bab0675` — этап 3; `448bef6` — доработка длительного запуска; `8badb1f` — этап 4; `7937be1` — этап 5 с отложенной приёмкой.

## 8. Что реализовано и проверено в этапе 5

### Наблюдаемость

Prometheus/Alertmanager, Grafana (22 панели), Tempo, ECS JSON stdout. 17 scrape targets: 6 Java-процессов, PostgreSQL, Redis, RabbitMQ, HAProxy, Garage, Tempo, OpenSearch exporter и 4 HTTP probes. Loki локально не поднят. Alertmanager не отправляет внешних сообщений.

Management 9091 — только внутри Docker. UI привязаны к loopback. HTTP/JVM/Hikari/business metrics; user/order/product IDs не используются как labels. Общие SQL gauges экспортируют четыре процесса: агрегировать MAX, не SUM. Stale business metrics контролируются отдельно.

W3C trace context сохраняется в SQL outbox, переносится AMQP headers и связывает consumer spans с исходным HTTP. HTTP к провайдеру имеет child span. Реальный trace через HTTP → outbox → Rabbit → consume.notifications проверен в Tempo, пример trace ID `5ea2f04a63ee468f9f8b495634d45732`. Sampling локально 10%, в целевой бюджетной модели 1% — это разные профили.

Canary worker-1 раз в 30 с меняет синтетический SKU 100000 и проверяет новую версию через Search API. Использовать histogram bucket ≤10 с, а не только success=true. Это выборка изменений, не все обновления каталога.

### Admission, idle polling, retention

- Новые checkout: ≥50000 unpublished outbox или oldest age ≥600 с → 503 OUTBOX_CAPACITY. Snapshot кэшируется до 5 с.
- Durable replay проверяется до admission; оплата/refund/release продолжаются. Guard не является жёсткой общей дисковой квотой и не покрывает весь admin traffic.
- Publisher idle poll 200 мс → максимум 2 с, активная пачка возвращает 200 мс; rebuild probe 5 с; topology declaration cache 30 с; AMQP RPC 3 с.
- Published outbox/inbox — 7 суток; webhook dedup — 30; быстрые idempotency_keys — 7. Cleanup batch 1000, SKIP LOCKED, отдельные короткие TX. Unpublished events не удаляются по возрасту.
- Retired search indexes: grace сутки, последние два сохраняются, alias проверяется; V6 даёт grace старым retired indexes.
- Media cleanup intents остаются tombstones для защиты от позднего PUT. Полного бессрочного exactly-once не обещаем.

Idle-замер: 633 SQL-вызова за 30,243 с = 20,93 SQL/с, включая health/exporters/canary/schedulers. Измеренного идентичного «до» нет, поэтому не заявлять точный коэффициент ускорения относительно теоретических 50 SQL/с.

### Миграции и ошибки API

Flyway/demo-seed вынесены в one-shot `migrate` до запуска API/workers. Migration job допускает SQL 60 с; рабочий SQL остаётся 2 с, Flyway в рабочих процессах выключен. Это устраняет конфликт холодного seed с рабочим timeout.

V5: trace_context, эксплуатационные индексы, retired_at. V6: grace legacy indexes. Существующие миграции не переписывать ради новой схемы; следующие изменения делать новой миграцией.

Исправлена классификация SQL outage: раньше 24 публичных запроса стали 401 через защищённый ERROR dispatch. Теперь resource failure → 503 DATABASE_UNAVAILABLE; отказ SQL-аутентификации → 503 AUTHENTICATION_UNAVAILABLE; неверный пароль при здоровой БД → 401. Followup подтвердил 32 публичных и 32 приватных 503, восстановление авторизации и инварианты 0.

Expiry gauge теперь считает только просрочку >10 с, вместо любого мгновения после expires_at. Отдельный опыт с остановленными workers: alert через 85,203 с, после возврата workers разрешился. Promtool проверяет правила, включая новый ApiLatencyHigh: Commerce p95 >200 мс / Search >300 мс в течение 2 мин.

34 модульных теста Commerce прошли Maven verify: JUnit Jupiter / AssertJ / Mockito. Java-код после этой проверки не менялся при последних исправлениях генератора. Новые JS/Python-настройки проверены статически, но не нагрузкой.

### Отказы и восстановление

Во время последовательных остановок шёл paced read traffic: **100 catalog + 20 search RPS**, не полный mixed 640. Целевыми операциями дополнительно проверялись checkout/payment/media. Генератор этого отказного опыта пропустил 207 arrivals — это сохранено.

| Компонент | Detection, с | Start → ready/drain, с |
| --- | ---: | ---: |
| Commerce-1 | 33,088 | 21,865 |
| Redis | 38,150 | 0,704 |
| RabbitMQ | 109,151 | 4,640 |
| OpenSearch | 35,546 | 16,743 |
| PostgreSQL graceful | 23,178 | 13,390 |
| Garage | 46,244 | 7,791 |
| Provider | 42,298 | 22,507 |

Start → recovery не включает предыдущий outage. Для Rabbit обнаружение зависит от возраста созданного outbox event. Потеря API давала 97 catalog 503 в окне исключения сервера; непрерывная безошибочность при отказе не доказана. Redis fallback сохранил чтение; OpenSearch отказ изолировал поиск; Garage cache HIT оставался доступен, MISS давал 502/504, upload 503; provider outage оставлял PENDING с последующей сверкой.

Отдельный SIGKILL PostgreSQL: PAID и replay сохранились, fsync/synchronous_commit включены, restart → ready 4,882 с, инварианты 0. Это crash recovery на том же томе.

PITR drill: pg_basebackup и pg_verifybackup, новый оплаченный заказ после base backup, согласованный SQL/S3 manifest при остановленных writers, WAL restore до именованной точки, исключение изменения после неё, отдельный Garage и сверка SHA-256. Результат: base backup/verify 3,981 с; recovery → API 17,560 с; 2 изображения / 240606 байт; fingerprints и HTTP-проверки совпали. Recovery project `hdls-recovery-b3278c82c0`, restore point `ecom_b3278c82c0`, LSN `0/29051C20`. Derived Search/Redis восстанавливаются отдельно.

WAL archive: timeout 60 с, temporary copy + sync + atomic rename + sync directory. Локальный архив расходует порядка 1 ГиБ/час при активности и хранится на том же Mac. Это не независимый региональный DR backup.

## 9. История нагрузочных неудач — не потерять при продолжении

| Опыт | Результат | Файл |
| --- | --- | --- |
| Ранний прогрев 60 с | 19 технических ошибок, 3 dropped | stage5_interrupted_run.json, initial_warmup_raw |
| Первый прерванный soak | Около 32 мин, OOM Tempo при 512 МиБ без достаточного ограничения Go; 4213 dropped и нарушения минутного p99 | stage5_interrupted_run.json |
| Java smoke без прогрева, 120 с | 76812 primary, ошибок/dropped 0, но imageUpload p95 411,70 мс на трёх загрузках | stage5_initial_smoke_results.json / stage5_smoke_results.json |
| Прогрев перед полным часом, 180 с | 115208 primary, ошибок/dropped 0, p95 2,20 / p99 5,66 мс | stage5_first_hour_results.json, warmup |
| Первый полный час, 3600 с | **Не пройден:** 2120936 primary, 589,15 фактического RPS, p95 210,51 / p99 1021,41 мс; 972 primary technical errors, 14 media errors, 9 support errors, 262320 dropped всех сценариев | stage5_first_hour_results.json |
| Проба 180 с, 133 VU / 50 мс | 114366 primary, ошибок 0, p95 2,77 / p99 30,28 мс, но 977 dropped; **не пройдена** | stage5_generator_results.json |
| Текущие 219 VU / 200 мс | Только статические проверки; нагрузка отложена пользователем | stage5_generator_config.json / stage5_status.json |

В полном часу инварианты/deadlocks/OOM были 0, 17 targets оставались доступными, но клиентские latency/throughput провалились. После примерно 20–25-й минуты k6 часто занимал около 200% CPU при quota 2 CPU и приближался к Go limit 1 ГиБ. Примерно 5,60 ГиБ — выборочный максимум Docker working set всего проекта с генератором. Сам генератор достигал около 1,2 ГиБ working set. **GC pressure — гипотеза, точного GC-профиля нет.** Отрицательные minima HTTP timings в raw-метриках также являются артефактом, который стоит исследовать; не удалять их молча. Подкачка/состояние Mac и возможные артефакты времени отдельно не изолированы.

## 10. Последние исправления: уже в Git, ещё не приняты нагрузкой

- Предвыделение k6: **718 → 219 VU**, исходя из 200 мс; параметр `VU_PREALLOC_SECONDS`.
- Верхний предел **1445 VU не изменён**. Веса операций и constant-arrival-rate сохранены: configured primary 640,0167 RPS, media 161 GET/с отдельно.
- k6: **2 CPU, cgroup 2 ГиБ, GOMEMLIMIT 1536MiB** вместо 1536 МиБ cgroup / Go 1 ГиБ.
- Для короткой проверки с той же плотностью метрик предусмотрен `METRIC_WINDOWS=60`.
- Каждые 30 с loader читает read-only REST API k6 внутри единственного one-off контейнера выбранного проекта. Собирает count, interval RPS, dropped, суммарные и минутные ошибки, p95 завершённой минуты. Читает cgroup v2 cpu.stat и memory.current; resource error отделён от клиентских счётчиков.
- RPS считается по monotonic timestamps клиентских snapshots; shortfall <98% от 640 после стартового окна помечается явно. В часе требуется хотя бы 60 валидных live client observations.
- В JSON k6 сохраняются preallocationSeconds, preallocatedVUs, maximumVUs, metricWindows — можно связать результат с настройками генератора.
- Tempo: 768 МиБ / Go 384MiB; Grafana: 384 МиБ / Go 192MiB; blackbox: 64 МиБ / Go 32MiB. Увеличение основано на пиках у прежних cgroup limits. Tempo blocks до 16 МиБ / 1 мин.
- Добавлен ApiLatencyHigh; Python/JS/Compose/promtool проверены без нагрузки.

Ни уменьшение VU, ни увеличение RAM сами по себе не доказывают исправление производительности. Проба 133 VU была слишком маленькой для приёмки и также пропускала arrivals. Не объявлять вариант 219 VU успешным до нового теста.

## 11. Где лежат доказательства и что означает статус

| Документ / JSON в docs | Как использовать |
| --- | --- |
| [stage5_status.json](stage5_status.json) | Канонический текущий статус: implementation_ready=true, load_acceptance_complete=false, deferred=true, current_generator.tested_under_load=false |
| [stage5_verification.md](stage5_verification.md) | Основной подробный отчёт и команды |
| [stage5_checks.json](stage5_checks.json) | Functional/fault evidence, initial issue и followup; passed здесь **не означает** успешную нагрузочную приёмку |
| [stage5_initial_checks.json](stage5_initial_checks.json) | Неизменённый исходный отказной опыт |
| [stage5_followup_results.json](stage5_followup_results.json) | Исправленные SQL/auth статусы и expiry alert |
| [stage5_restore_results.json](stage5_restore_results.json) | PITR/S3 drill |
| [stage5_idle_results.json](stage5_idle_results.json) | Idle SQL и эксплуатационные уточнения |
| [stage5_first_hour_results.json](stage5_first_hour_results.json) | Архив неуспешного полного часа, сохранять |
| [stage5_first_hour_resource_results.json](stage5_first_hour_resource_results.json) | Ресурсы того часа, сохранять |
| [stage5_load_results.json](stage5_load_results.json) | Последний полный час; сейчас совпадает с неуспешным первым, loader перезаписывает этот путь |
| [stage5_resource_results.json](stage5_resource_results.json) | Сводка последнего полного часа |
| [stage5_generator_results.json](stage5_generator_results.json) | Пока историческая проба 50 мс / 133 VU; не путать с текущими настройками |
| [stage5_generator_config.json](stage5_generator_config.json) | k6 inspect: текущий часовой сценарий 219/1445 VU; не результат нагрузки |
| [stage5_interrupted_run.json](stage5_interrupted_run.json) | Исходный OOM/прерванный опыт, включая raw metrics |

`analyze_stage5.py --check` проверяет согласованность README/документа с JSON, **не приёмку нагрузки**. `--require-passed` — отдельный gate, сейчас должен завершаться неуспехом. Он требует status flags, успешного нового часа и smoke, actual duration ≥3600, dropped/invariants/deadlocks/OOM 0, ≥60 client snapshots, 17 healthy targets и совпадения preallocatedVUs с inspect.

## 12. Состояние данных на Mac и ограничения переноса

Локальные артефакты: `/Users/maria.ignatova/mipt-hw/hlds/artifacts/stage5/`. Подробные k6 JSON/logs/resources — в `load/`; backups, manifest и байты объектов — в `backups/`; текущие live snapshots — `soak-live.json`. История операторских снимков первого часа сохранена как `first-hour-resource-live.json`; рабочий resource-live.json был сброшен для следующего измерения.

Эти каталоги исключены .gitignore. На чистом clone нельзя запускать analyze_stage5_resources.py над старым отчётом и ожидать локальный файл `*-resources.json`: нужны исходные локальные артефакты либо новый проведённый тест. Tracked JSON достаточно для чтения и обычных `--check`.

Сохранённые тома проверочного проекта:

```text
hdls-stage5-verify_postgres-data
hdls-stage5-verify_rabbit-data
hdls-stage5-verify_search-data
hdls-stage5-verify_garage-meta
hdls-stage5-verify_garage-data
hdls-stage5-verify_wal-archive
hdls-stage5-verify_prometheus-data
hdls-stage5-verify_grafana-data
hdls-stage5-verify_tempo-data
hdls-stage5-verify_alerts-data
```

Recovery volumes: `hdls-recovery-b3278c82c0_recovered`, `..._garage-meta`, `..._garage-data`. Исходные stage4/первые stage5 volumes также не удалялись. Не считать их мусором. Стенд остановлен, чтобы не расходовать WAL/archive и ресурсы Mac в ожидании пользователя.

## 13. Команды безопасного ознакомления — без нагрузки

Все команды из корня репозитория. Сначала прочитать файлы, затем выполнять только необходимые проверки.

```bash
python3 scripts/calculate_capacity.py --check
python3 scripts/analyze_stage3_followup.py --check
python3 scripts/analyze_stage4.py --check
python3 scripts/analyze_stage5.py --check
python3 -m compileall -q scripts infra/stage5/os_exporter.py
git diff --check
```

Compose-переменные нужно задавать явно: default STAGE5_PROJECT в скриптах — **hdls-stage5**, а сохранённый стенд — **hdls-stage5-verify**.

```bash
export STAGE5_PROJECT=hdls-stage5-verify
C=(docker compose -p "$STAGE5_PROJECT" -f infra/stage4/compose.yaml -f infra/stage5/compose.yaml)
"${C[@]}" config --quiet
"${C[@]}" ps -a
```

Статическая проверка k6, без выполнения HTTP-функций:

```bash
"${C[@]}" run --rm --no-deps -T -e SECONDS=3600 k6 \
  inspect --include-system-env-vars --execution-requirements /scripts/load.js
```

`--include-system-env-vars` важен: без него inspect может показать default 120 с, игнорируя container env, хотя настоящий run получит 3600 с. Проверка должна показывать 219 preallocated / 1445 max VU и duration 1h.

Promtool unit tests, без нагрузки на магазин:

```bash
docker run --rm --entrypoint=promtool \
  -v "$PWD/infra/stage5:/etc/prometheus:ro" \
  prom/prometheus:v3.5.0 test rules /etc/prometheus/alerts_test.yaml
```

Test-файл не смонтирован в работающий Prometheus, поэтому команда exec promtool /etc/prometheus/alerts_test.yaml там не сработает. Использовать отдельный read-only mount, как выше.

## 14. После нового разрешения пользователя: порядок продолжения

### A. Подготовка и проверка клиента

1. Перепроверить текущий Git status и конфигурации; не потерять пользовательские правки. Прочитать статус и этот handoff.
2. Проверить свободную память/диск Mac, активность swap/compression, Docker VM и постороннюю нагрузку. Не закрывать чужие приложения/контейнеры без указания пользователя.
3. Сохранить исторические JSON перед любым перезаписыванием. Использовать новый output-name для экспериментальной пробы.
4. Если образов нет или Java-код изменился, собрать; иначе не пересобирать бессмысленно. Затем инициализировать сохранённый проект.

```bash
"${C[@]}" build commerce-1 search provider
STAGE5_PROJECT=hdls-stage5-verify python3 scripts/init_stage5.py
```

Init сохраняет данные, поднимает зависимости, создаёт pg_stat_statements при необходимости, инициализирует Garage, ждёт API и 17 scrapes. API/workers стартуют после migrate job, exited 0 у migrate — штатное завершение.

5. Провести короткую проверку с 60 минутными окнами метрик, отдельным прогревом и текущими 219 VU. Пример **для будущего разрешённого запуска**, сейчас не выполняется:

```bash
METRIC_WINDOWS=60 STAGE5_PROJECT=hdls-stage5-verify caffeinate -i \
  python3 scripts/load_stage5.py --seconds 180 --warmup-seconds 180 \
  --output-name stage5_generator_candidate_results.json
```

6. Смотреть не только up: interval RPS около 640, dropped=0, ошибки, p95, CPU/throttling/RAM самого k6, целевых сервисов и хоста. При неполадках сначала изолировать генератор/хост от bottleneck приложения. Не тратить ещё час, наблюдая только живость сервисов.

### B. Часовой mixed и финальные проверки

Если короткая проверка и ресурсы удовлетворительны, после разрешения на эту нагрузку:

```bash
STAGE5_PROJECT=hdls-stage5-verify caffeinate -i \
  python3 scripts/load_stage5.py --seconds 3600
```

По умолчанию перед ним ещё 180 с отдельного прогрева. Во время измерения не запускать сборки, restore или fault injection. Не снижать RPS/не менять состав операций ради зелёного результата; диагностические профили меньшей нагрузки допустимы только как отдельные, явно подписанные опыты.

Критерии текущего k6-профиля: technical/operation/support/media errors <0,1%; dropped=0; общий HTTP p95<200/p99<500 мс; checkout p95<500/p99<1000; search p95<300; payment/webhook p95<500; остальные не-checkout p95<200, p99<1000; media p95<200. Минутные p95/p99/technical error thresholds также проверяются. Точный источник — load_stage5.js. Отдельно: SQL invariants/deadlocks/OOM 0, достаточная полнота client observations, healthy scrapes, outbox drain.

После часа нужен финальный smoke на текущей версии, например 180 с с `--warmup-seconds 0 --output-name stage5_smoke_results.json`, и здоровая проверка свежего trace через HTTP → outbox → Rabbit → consumer. Проверить реальное состояние DLQ, outbox, REFUND_PENDING, readiness, актуальность business metrics. Valid PENDING_PAYMENT с ещё не истёкшими резервами допустимы — не требовать пустоты всех заказов.

Полные fault/restore scripts намеренно останавливают зависимости: `check_stage5.py`, `check_stage5_followup.py`, `restore_stage5.py`. Не запускать их параллельно здоровому soak и не повторять тяжёлые уже пройденные опыты без новой причины. Если изменения затронут транзакционные/платёжные гарантии, выбрать соответствующую содержательную регрессию.

### C. Отчётность и итог

До новых измерений проверить текущие предположения анализаторов:

- analyze_stage5.py пока подписывает stage5_generator_results.json как историческую пробу 50 мс. Не перезаписывать его новым опытом 219 VU без архива и изменения подписи/логики. Для candidate выше выбран новый путь.
- stage5_first_hour_results/resource_results сохранять как историю; latest stage5_load_results.json можно обновлять новым выполненным часом.
- Текущий stage5_smoke_results.json — старый неуспешный Java smoke. Будущий финальный smoke должен заменить latest с сохранением исторической копии.
- Статус JSON и генерация README не переключаются автоматически только от факта выполнения теста. Обновлять tested_under_load/load_acceptance_complete/deferred и remaining лишь после оценки доказательств. Если в шаблонах остались исторические формулировки, обновить их вместе с данными.
- Resource analyzer отдельно сообщает generator cgroup memory.current и Docker working set. Не складывать несовпадающие/разновременные максимумы. Без операторских общих snapshots total with generator может быть null — это честно, не ошибка, которую следует заполнять выдуманным числом.

После принятого результата:

```bash
python3 scripts/analyze_stage5_resources.py
python3 scripts/analyze_stage5.py --write
python3 scripts/analyze_stage5.py --check --require-passed
python3 scripts/calculate_capacity.py --check
python3 scripts/analyze_stage3_followup.py --check
python3 scripts/analyze_stage4.py --check
git diff --check
```

Проверить ссылки, JSON/$ref, согласованность README, runtime_resources, reliability и фактических конфигураций. Сохранить все существенные неудачи и ограничения. Остановить свой стенд с сохранением томов, если постоянная работа не нужна:

```bash
"${C[@]}" stop
```

Затем commit/push согласованных изменений, проверить чистый checkout и равенство main/origin/main. Не объявлять этап принятым, если пороги всё ещё не пройдены; описать измеренный предел, изолированную причину и что именно остаётся проверить.

## 15. Порты и demo-доступ после запуска

| Интерфейс | Адрес |
| --- | --- |
| HAProxy Commerce gateway | localhost:18080 |
| Прямые Commerce API | localhost:18081, localhost:18082 |
| Search API / media / provider | localhost:18085 / 18086 / 18087 |
| OpenSearch / Garage S3 | localhost:19200 / 13900 |
| Grafana | http://localhost:13000/d/ecom-stage5 |
| Prometheus / Tempo / Alertmanager | localhost:19090 / 13200 / 19093 |
| RabbitMQ UI | localhost:15672 |

Только тестовые credentials: buyer-N/demo-buyer-N (N 1..1000), admin/demo-admin; Grafana admin/local-grafana-password; Rabbit commerce/local-rabbit-password. S3/provider/HMAC demo tokens заданы в Compose. Они не являются production secrets. Не публиковать стенд наружу как готовый магазин. Management 9091 не выставлен на хост. Пока проект остановлен, URL не обязаны отвечать.

## 16. Ограничения выводов и следующий архитектурный этап

- Один Mac с одноузловыми PostgreSQL/Rabbit/OpenSearch/Garage не доказывает устойчивость к потере узла/AZ/региона. Promotion/fencing/HA manager не реализованы локально.
- Архитектурные RPO 0 при оговорённой потере primary и RTO≤5 мин; региональные RPO≤5 мин/RTO≤1 ч — цели, не измеренные достижения.
- Backup/WAL/S3 logical copy на том же Mac не независимый DR. Restore 17,56 с относится к малому dataset и не включает инфраструктуру, DNS, fencing, внешнюю финансовую сверку.
- Provider stub откатывается вместе с локальной SQL; реальный эквайринг так не работает.
- k6 latency — response.timings.duration, без blocked/DNS/connect/TLS и реальной WAN. Нельзя складывать перцентили фаз.
- Media-профиль повторяет один объект через локальный Nginx cache; он не доказывает 161 uncached S3 GET/с, CDN-географию или большой working set. ImageUpload — маленький PNG.
- Demo Basic/компактные данные/разделённые группы пользователей ограничивают переносимость performance. Flash sale и корректность конфликтов проверяются отдельно.
- Один час не доказывает месячный SLO или отсутствие долгосрочной утечки. Занятый swap без дельт не доказывает текущую подкачку; CPU/RAM корреляция не является профилем GC.

После завершения текущей приёмки: сверить всю цепочку нагрузка → данные → ресурсы → надёжность; разобрать рост 5–10×, cache hit degradation и потерю AZ; уточнить триггеры масштабирования; добавить обоснованную стоимость compute/RAM/disks/replicas/backup/CDN/egress/observability по выбранному провайдеру; подготовить защиту до 10 минут. Не выдумывать тарифы и достигнутые SLA. Подробные требования финальной части — project_plan.md.

## 17. Рекомендуемый первый ответ новой LLM

«Прочитал handoff и статус. Этап 5 реализован в коде, но нагрузочная приёмка не завершена. Новую нагрузку пока не запускаю: предыдущая просьба пользователя отложить её остаётся в силе. Сначала сверю текущий commit и файлы, проверю необходимые правки/отчётность, затем опишу готовность к отдельному разрешённому прогону».
