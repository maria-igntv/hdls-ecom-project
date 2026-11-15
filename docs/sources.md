# Источники и основания решений

Реестр отделяет требования курса, принятые решения, модельные допущения и рыночные данные. Код источника используется в [реестре параметров](assumptions.md).

## Материалы и решения

| Код | Источник | Что подтверждает |
| --- | --- | --- |
| S01 | Задание курса «Распределённые высоконагруженные системы», локальный файл `lectures/assignment_checkpoints.docx` | Аудитория от 1 млн MAU, география, 2–5 функций MVP, README в публичном репозитории, пять этапов и защита до 10 минут |
| S02 | Программа курса, локальный файл `lectures/lectures_description.docx` | Темы занятий и ожидаемые знания |
| S03 | [Согласованный рабочий план](../project_plan.md) | Собственный магазин, начальная архитектура, исходные модельные числа и порядок работы |
| S04 | Решения пользователя в обсуждении проекта | Java как рабочий язык; принятые границы компонентов; наличие Mac M4 Pro, ВМ и 100 ГБ в Облаке Mail |
| S05 | [Требования Spring Boot](https://docs.spring.io/spring-boot/system-requirements.html) | Совместимость Java и Spring Boot; не доказательство производительности приложения |
| S06 | [Garage Quick Start](https://garagehq.deuxfleurs.fr/documentation/quick-start/) и [совместимость S3](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/) | Возможность самостоятельного S3-совместимого стенда и ограничения одноузловой конфигурации |
| S07 | [Свой endpoint в AWS SDK for Java](https://docs.aws.amazon.com/sdk-for-java/latest/developer-guide/endpoint-config.html) | Настройка клиента на стороннее S3-совместимое хранилище |
| S08 | [WebDAV в Облаке Mail](https://help.mail.ru/cloud/desktop/webdav/) | Документированный способ доступа и пароль приложения; не наличие S3-бакета |
| S09 | [Границы продукта](product_scope.md) | Проектные решения о ролях, валюте, операциях и корректности |

S03 и S09 — внутренние основания модели, а не независимые доказательства рыночного спроса или аудитории. S04 фиксирует сообщения пользователя; характеристики оборудования и свободная квота не проверялись инструментально. Технические страницы S05–S08 изучены при выборе Java и варианта S3 в обсуждении проекта.

Материалы курса находятся в локальной папке `lectures`, которая исключена из Git. Описание требований сохранено в README; локальные названия выше позволяют сопоставить их с выданным заданием.

## Первичные источники чекпоинта 1

| Код | Источник | Период/назначение | Применение |
| --- | --- | --- | --- |
| S10 | [М.Видео: результаты 2025 года](https://www.mvideoeldorado.ru/ru/press-centr/press-relizy/detail/4211) | Публикация 30.04.2026, отчёт за 2025 год | Онлайн-продажи 134,8 млрд руб., клиентская база 80,9 млн; учтена смена методики |
| S11 | [Ozon: результаты 2025 года](https://ir.ozon.com/ru/sth/ozon-obyavlyaet-finansovye-rezultaty-za-chetvertyy-kvartal-2025-goda-i-2025-god-18833bbf) | Публикация 26.02.2026, отчёт за 2025 год | 65,1 млн активных покупателей, 2,481 млрд заказов, 43 млн среднесуточного охвата в декабре; это разные метрики |
| S12 | [АКИТ: итоги 2025 года](https://akit.ru/news/11-5-trln-rublej-akit-podvela-itogi-internet-torgovli-za-2025-god) | Публикация 19.02.2026, рынок за 2025 год | Рынок и региональные доли оборота; proxy географии пользователей помечен допущением |
| S13 | [HAProxy 3.2 manual](https://docs.haproxy.org/3.2/configuration.html), [health checks](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/reliability/health-checks/) | Документация выбранной ветки и active HTTP checks | Round-robin, limits, timeout, readiness, fall/rise |
| S14 | [Nginx load balancing](https://nginx.org/en/docs/http/load_balancing.html) | Документация Nginx | Пассивные проверки OSS и сравнение с HAProxy; стандартные active checks относятся к Plus |
| S15 | [Mediascope: Большая маленькая Россия](https://www.mediascope.net/upload/iblock/806/62bc19uf64iehj79jdeez36nu1brewrw/Mediascope_DBD_%D0%A7%D0%B5%D1%81%D1%82%D0%BD%D1%8B%D1%85.pdf), слайды 7–8 | Cross Web, февраль 2025, население 12+, mobile/desktop | Первичный источник определений месячного/суточного охвата и присутствия профильных магазинов |

Разбор применимости данных: [исследование аналогов](market_research.md). Источники подтверждают нишу и масштаб; целевые MAU/DAU, частоты, конверсии, размеры и кэши нашего сервиса — допущения. Полные входные числа находятся в [модели](../model/capacity.json), результаты — в [расчёте](capacity.md). Similarweb не используется.

## Источники чекпоинта 2

| Код | Источник | Применение |
| --- | --- | --- |
| S16 | [Spring Boot 3.5 requirements](https://docs.spring.io/spring-boot/3.5/system-requirements.html) | Выбранная ветка 3.5.16 совместима с Java 25; пакет реально собран и запущен |
| S17 | [PostgreSQL 17 locking](https://www.postgresql.org/docs/17/explicit-locking.html), [EXPLAIN](https://www.postgresql.org/docs/17/using-explain.html) | Row locks, порядок доступа, отличие cost от actual time и выбор индексов |
| S18 | [SQL dump](https://www.postgresql.org/docs/17/backup-dump.html), [continuous archiving](https://www.postgresql.org/docs/17/continuous-archiving.html), [pgBackRest](https://pgbackrest.org/user-guide.html) | Логический restore стенда отделён от планируемого физического backup/WAL/PITR |

Схема, SQL-планы и фактические измерения собственного прототипа находятся в [storage.md](storage.md) и [проверке этапа 2](stage2_verification.md). Конфигурации целевых серверов остаются оценками до профилирования.

## Правила использования внешних данных

1. Фиксируем исходную публикацию и период наблюдения; дату доступа не выдаём за дату публикации.
2. Сохраняем определение метрики: посетитель, активный пользователь, покупатель, заказ и попытка оформления — разные сущности.
3. Разделяем данные рынка/аналога и выбранные параметры собственного магазина.
4. Если точной величины нет, помечаем допущение, объясняем основание и проверяем диапазон значений.
5. Сводим числа в единых единицах и указываем, относится показатель к онлайн-продажам, стране, группе компаний или отдельному сервису.
