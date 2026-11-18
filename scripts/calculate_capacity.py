#!/usr/bin/env python3
"""Reproduce stage 1 tables using only the Python standard library."""
import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400
GB = 10**9
TB = 10**12


def number(value, digits=2):
    return f"{value:,.{digits}f}".replace(",", " ")


def table(headers, rows):
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
        *("| " + " | ".join(map(str, row)) + " |" for row in rows),
    ])


def calculate(model):
    a, c, p, s, media = (model[k] for k in ("audience", "commerce", "peak", "storage", "media"))
    assert math.isclose(sum(r["share"] for r in model["regions"]), 1)
    assert 0 < a["dau"] <= a["mau"] <= a["registered_users"]
    assert all(0 <= c[k] <= 1 for k in ("create_success", "payment_initiated", "payment_paid"))
    assert all(0 <= media[k] <= 1 for k in ("cdn_hit", "api_card_hit", "list_network_fraction", "card_network_fraction"))
    rows = []
    base = {}
    for op in model["operations"]:
        op = op.copy()
        op["base"] = a["dau"] * op["actions"]
        base[op["id"]] = op["base"]
        op["daily"] = op["base"] * (1 + op["retry"])
        rows.append(op)
    created = base["OP06"] * c["create_success"]
    initiated = created * c["payment_initiated"]
    paid = initiated * c["payment_paid"]
    expired = created - paid
    extra = [
        ("OP07", "Инициация оплаты", initiated, c["payment_retry"], 1000, 1000),
        ("OP08", "Webhook оплаты", initiated * c["webhooks_per_initiation"], 0, 2000, 300),
        ("OP11", "Изменения каталога", c["catalog_updates_day"], 0, 5000, 1000),
        ("OP12", "Корректировки запаса", c["stock_updates_day"], 0, 1000, 300),
        ("OP13", "Загрузки изображений", c["image_uploads_day"], 0, model["catalog"]["original_bytes"] + 500, 500),
    ]
    for ident, name, daily, retry, req, resp in extra:
        rows.append(dict(id=ident, name=name, base=daily, daily=daily * (1 + retry), retry=retry,
                         request_bytes=req, response_bytes=resp, service="Commerce"))
    requests = sum(r["daily"] for r in rows)
    ingress = sum(r["daily"] * r["request_bytes"] for r in rows)
    egress = sum(r["daily"] * r["response_bytes"] for r in rows)
    by_id = {r["id"]: r for r in rows}
    thumb_requests = (base["OP01"] + base["OP03"]) * media["list_images"] * media["list_network_fraction"]
    detail_requests = base["OP02"] * media["card_images"] * media["card_network_fraction"]
    image_requests = thumb_requests + detail_requests
    cat = model["catalog"]
    image_bytes = thumb_requests * cat["thumbnail_bytes"] + detail_requests * cat["detail_bytes"]
    notifications = created + paid + expired
    inventory_events = 2 * created * c["items_per_order"] + c["stock_updates_day"]
    search_events = c["catalog_updates_day"] + inventory_events
    events = notifications + search_events
    query_calls = initiated * c["reconcile_fraction"]
    costs = model["sql"]
    private_requests = sum(x["daily"] for x in rows if x["id"] in {"OP04","OP05","OP06","OP07","OP09","OP10","OP11","OP12","OP13"})
    sql = (
        by_id["OP01"]["daily"] + by_id["OP02"]["daily"] * (1 - media["api_card_hit"])
        + costs["cart_read"] * by_id["OP04"]["daily"] + costs["cart_write"] * by_id["OP05"]["daily"]
        + costs["checkout_success"] * created + costs["checkout_rejected"] * (base["OP06"] - created)
        + costs["checkout_replay"] * (by_id["OP06"]["daily"] - base["OP06"])
        + costs["payment_init"] * by_id["OP07"]["daily"] + costs["payment_webhook"] * by_id["OP08"]["daily"]
        + costs["history_read"] * by_id["OP09"]["daily"] + costs["order_detail"] * by_id["OP10"]["daily"]
        + costs["catalog_update"] * c["catalog_updates_day"] + costs["stock_update"] * c["stock_updates_day"] + costs["image_upload"] * c["image_uploads_day"]
        + costs["expiry_per_order"] * expired + costs["payment_reconcile"] * query_calls
        + costs["message_handling"] * events + costs["search_projection"] * search_events
        + DAY / costs["publisher_poll_interval_seconds"] + private_requests
    )
    redis = requests + by_id["OP02"]["daily"] * (2 - media["api_card_hit"])
    return dict(rows=rows, by_id=by_id, base=base, created=created, initiated=initiated, paid=paid,
                expired=expired, requests=requests, ingress=ingress, egress=egress,
                thumb_requests=thumb_requests, detail_requests=detail_requests,
                image_requests=image_requests, image_bytes=image_bytes, image_ingress=image_requests * media["image_request_bytes"],
                events=events, search_events=search_events, notifications=notifications,
                query_calls=query_calls, sql=sql, redis=redis)


def storage(model, result, years):
    a, cat, c, s = (model[k] for k in ("audience", "catalog", "commerce", "storage"))
    growth = (1 + a["annual_growth"]) ** (years - 1)
    sku = cat["sku"] * (1 + cat["annual_growth"]) ** (years - 1)
    first_retained_year = max(0, years - s["orders_retention_years"])
    orders = result["created"] * 365 * sum((1 + a["annual_growth"]) ** y for y in range(first_retained_year, years))
    profiles = a["registered_users"] * growth
    carts = profiles * s["stored_cart_fraction_registered"]
    logical = []

    def add(name, count, size, copies=1, overhead=1, family="sql"):
        logical.append(dict(name=name, count=count, logical=count * size,
                            physical=count * size * copies * overhead, family=family))

    add("Профили", profiles, s["profile_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Карточки", sku, s["product_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Цены и запас", sku, s["price_inventory_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Метаданные изображений", sku * cat["images_per_sku"], s["image_metadata_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Корзины", profiles, s["cart_header_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Позиции корзин", carts * s["cart_items"], s["cart_item_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Заказы", orders, s["order_header_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Позиции заказов", orders * c["items_per_order"], s["order_item_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Платежи", orders * c["payment_initiated"], s["payment_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Резервы с позициями", result["created"] * growth * s["reservation_retention_days"], s["reservation_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Ключи создания заказа", result["base"]["OP06"] * growth * s["idempotency_retention_days"], s["idempotency_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Outbox", result["events"] * growth * s["outbox_retention_days"], s["event_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Inbox", result["events"] * growth * s["inbox_retention_days"], s["inbox_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Дедупликация webhook", result["by_id"]["OP08"]["daily"] * growth * s["webhook_retention_days"], s["webhook_dedup_bytes"], s["sql_copies"], s["sql_overhead"])
    add("Поисковые документы", sku, s["search_document_bytes"], s["search_copies"], s["search_index_multiplier"], "search")
    add("Кэш карточек", sku * s["cache_sku_fraction"], s["cache_value_bytes"], s["cache_copies"], s["cache_overhead"], "cache")
    add("Исходные изображения", sku * cat["images_per_sku"], cat["original_bytes"], family="media")
    add("Превью двух размеров", sku * cat["images_per_sku"] * 2, (cat["thumbnail_bytes"] + cat["detail_bytes"]) / 2, family="media")
    add(f'Очередь при простое {s["queue_outage_hours"]} часов', result["events"] * growth * s["queue_outage_hours"] / 24, s["event_bytes"], s["queue_copies"], s["queue_overhead"], "queue")
    add(f'Логи API и workers за {s["logs_days"]} суток', (result["requests"] + result["events"]) * growth * s["logs_days"], s["log_bytes_per_request"] * s["logs_compression"], s["observability_copies"], family="logs")
    add(f'Спаны трассировки за {s["traces_days"]} суток', result["requests"] * growth * s["trace_sample"] * s["spans_per_trace"] * s["traces_days"], s["span_bytes"] * s["traces_compression"], s["observability_copies"], family="traces")
    add(f'Точки метрик за {s["metric_days"]} суток', s["metric_series"] * DAY / s["metric_interval_seconds"] * s["metric_days"], s["metric_bytes_per_sample"], overhead=s["metric_overhead"], family="metrics")
    sql_one_copy = sum(x["logical"] for x in logical if x["family"] == "sql") * s["sql_overhead"]
    media_copy = sum(x["logical"] for x in logical if x["family"] == "media")
    backups = (sql_one_copy * s["backup_full_copies"] * s["backup_compression"] + media_copy
               + result["created"] * growth * s["wal_bytes_per_created_order"] * s["wal_days"])
    return logical, backups


def report(model):
    r = calculate(model)
    a, p, cat, media, s = (model[k] for k in ("audience", "peak", "catalog", "media", "storage"))
    factor = p["factor"]
    one, backup1 = storage(model, r, 1)
    three, backup3 = storage(model, r, 3)
    out = ["# Расчёт нагрузки и хранения", "",
           "Таблицы вычислены из [capacity.json](../model/capacity.json). Все параметры поведения, размеров и эффективности кэшей — допущения; это расчётная модель, а не результат нагрузочного тестирования.", "",
           "Из корня репозитория: `python3 scripts/calculate_capacity.py --write` обновляет этот файл и сводку README; `python3 scripts/calculate_capacity.py --check` проверяет их актуальность. Пакеты устанавливать не требуется.", "",
           "## География", "",
           "Россия — 100%. Доли оборота регионов из S12 используются как модельный proxy долей MAU/DAU. Это допущение о нашей аудитории, а не измерение пользователей АКИТ. Округление остатка выполняется в группе остальных регионов. Часовые пояса не используются для искусственного уменьшения общего пика.", "",
           table(["Регион", "Доля модели", "MAU", "DAU"], [[x["name"], number(x["share"] * 100, 1) + "%", number(a["mau"] * x["share"], 0), number(a["dau"] * x["share"], 0)] for x in model["regions"]]), "",
           "## Продуктовые метрики", "",
           table(["Показатель", "Значение", "Основание"], [
               ["MAU / DAU", f'{number(a["mau"], 0)} / {number(a["dau"], 0)}', "Цель / допущение"],
               ["Зарегистрировано", number(a["registered_users"], 0), "Отдельная накопленная база; допущение"],
               ["Попытки / созданные / оплаченные заказы в сутки", f'{number(r["base"]["OP06"], 0)} / {number(r["created"], 0)} / {number(r["paid"], 0)}', "90% создания; 96% инициации; 90% успешных оплат"],
               ["Созданные заказы за первый год", number(r["created"] * 365, 0), "365 суток исходной нагрузки"],
               ["Каталог / исходные медиаобъекты", f'{number(cat["sku"], 0)} / {number(cat["sku"] * cat["images_per_sku"], 0)}', "100 тыс. SKU × 4"],
           ]), "",
           "### Действия на одного DAU", "",
           table(["Операция", "Действий/DAU/сутки", "Первичных действий/сутки"], [[op["id"] + " " + op["name"], number(op["actions"]), number(op["actions"] * a["dau"], 0)] for op in model["operations"]]), "",
           "## API RPS и трафик", "",
           f'RPS_avg = requests_day / 86 400; RPS_peak = RPS_avg × {factor}. В строках уже учтены повторы; повторно умножать итог на retry-factor нельзя. Размеры — приближённые байты HTTP без TCP/TLS и репликации; для сети предусмотрен отдельный запас {number((p["network_margin"] - 1) * 100, 0)}%.', "",
           table(["Операция", "Запросов/сутки", "Средний RPS", "Пиковый RPS", "Вход, ГБ/сутки", "Выход, ГБ/сутки"], [
               [x["id"] + " " + x["name"], number(x["daily"]), number(x["daily"] / DAY), number(x["daily"] / DAY * factor), number(x["daily"] * x["request_bytes"] / GB, 4), number(x["daily"] * x["response_bytes"] / GB, 4)] for x in r["rows"]
           ] + [["Итого API", number(r["requests"]), number(r["requests"] / DAY), number(r["requests"] / DAY * factor), number(r["ingress"] / GB, 4), number(r["egress"] / GB, 4)]]), "",
           "Инициация оплаты: created × 0,96 × 1,05. Webhooks: initiated × 1,25; коэффициент уже включает дубликаты. Входящие загрузки изображений входят в OP13. Платёжная страница провайдера не входит в наш трафик. 5000 изменений каталога и 1000 изменений запаса за сутки — административные/импортные операции, а не дополнительные пользователи.", "",
           "## Изображения и сеть", "",
           "Список категории и поиска содержит 20 превью по 30 КБ; после браузерного кэша по сети запрашивается 50%. Карточка загружает 3 изображения по 120 КБ; доля сетевых загрузок — 70%. Четвёртое исходное изображение хранится, но в среднем не открывается. Повторный JSON-запрос не запускает автоматически повтор всех картинок: браузерная доля применяется к первичным просмотрам.", "",
           table(["Поток", "Запросов/сутки", "RPS avg / peak", "Данные, ГБ/сутки", "Пик, Мбит/с"], [
               ["CDN → браузеры, изображения", number(r["image_requests"], 0), f'{number(r["image_requests"] / DAY)} / {number(r["image_requests"] / DAY * factor)}', number(r["image_bytes"] / GB), number(r["image_bytes"] * 8 / DAY / 10**6 * factor)],
               ["Origin → CDN при hit 95%", number(r["image_requests"] * (1 - media["cdn_hit"]), 0), f'{number(r["image_requests"] * (1 - media["cdn_hit"]) / DAY)} / {number(r["image_requests"] * (1 - media["cdn_hit"]) / DAY * factor)}', number(r["image_bytes"] * (1 - media["cdn_hit"]) / GB), number(r["image_bytes"] * (1 - media["cdn_hit"]) * 8 / DAY / 10**6 * factor)],
               ["Вход CDN, image GET", number(r["image_requests"], 0), "См. выдачу", number(r["image_ingress"] / GB), number(r["image_ingress"] * 8 / DAY / 10**6 * factor)],
               ["Вход origin, image GET при hit 95%", number(r["image_requests"] * (1 - media["cdn_hit"]), 0), "См. выдачу", number(r["image_ingress"] * (1 - media["cdn_hit"]) / GB), number(r["image_ingress"] * (1 - media["cdn_hit"]) * 8 / DAY / 10**6 * factor)],
               ["API вход", number(r["requests"], 0), "См. API", number(r["ingress"] / GB), number(r["ingress"] * 8 / DAY / 10**6 * factor)],
               ["API выход", number(r["requests"], 0), "См. API", number(r["egress"] / GB), number(r["egress"] * 8 / DAY / 10**6 * factor)],
           ]), "",
           f'Бюджет исходящей сети API + media origin с запасом 30%: **{number((r["egress"] + r["image_bytes"] * (1 - media["cdn_hit"])) * 8 / DAY / 10**6 * factor * p["network_margin"])} Мбит/с**. Это агрегат origin, а не CDN и не обещание сетевой ёмкости ВМ. Репликация, бэкапы, брокер и межсервисный трафик требуют добавочного бюджета на следующих этапах.', "",
           "Входящие image GET учтены по 500 байт на объект. При CDN miss origin получает только долю этих запросов. Выдача медиа в таблице считается по телам изображений; ответные заголовки и TCP/TLS покрываются сетевым запасом, а не считаются точным измерением.", "",
           "## Внутренние компоненты", "",
           table(["Компонент/операция", "В сутки", "Среднее/с", "Пик/с"], [
               ["Commerce API", number(sum(x["daily"] for x in r["rows"] if x["service"] == "Commerce"), 0), number(sum(x["daily"] for x in r["rows"] if x["service"] == "Commerce") / DAY), number(sum(x["daily"] for x in r["rows"] if x["service"] == "Commerce") / DAY * factor)],
               ["Search API / поисковые запросы", number(r["by_id"]["OP03"]["daily"], 0), number(r["by_id"]["OP03"]["daily"] / DAY), number(r["by_id"]["OP03"]["daily"] / DAY * factor)],
               ["Redis GET/SET и rate-limit, верхняя модель", number(r["redis"], 0), number(r["redis"] / DAY), number(r["redis"] / DAY * factor)],
               ["SQL-операции, грубая оценка", number(r["sql"]), number(r["sql"] / DAY), number(r["sql"] / DAY * factor)],
               ["Outbox / публикации в брокер", number(r["events"], 0), number(r["events"] / DAY), number(r["events"] / DAY * factor)],
               ["Обновления поиска", number(r["search_events"], 0), number(r["search_events"] / DAY), number(r["search_events"] / DAY * factor)],
               ["Уведомления", number(r["notifications"], 0), number(r["notifications"] / DAY), number(r["notifications"] / DAY * factor)],
               ["Исходящие платёжные вызовы, включая сверку", number(r["by_id"]["OP07"]["daily"] + r["query_calls"]), number((r["by_id"]["OP07"]["daily"] + r["query_calls"]) / DAY), number((r["by_id"]["OP07"]["daily"] + r["query_calls"]) / DAY * factor)],
               ["Истечение резервов", number(r["expired"], 0), number(r["expired"] / DAY), number(r["expired"] / DAY * factor)],
           ]), "",
           "SQL-модель уточнена по реализации этапа 2: категория 1; miss карточки 1; корзина read 3/write 7; успешный checkout с 3 SKU — 29, бизнес-отказ 7, повтор до 3; история 1, детали 2; каталог/запас 5. Добавлена одна SQL-проверка учётной записи на приватный запрос в текущей аутентификации. Стоимости оплаты, обработки сообщений и целевого polling остаются допущениями раздела sql в JSON. Это число обращений, а не одинаковых по стоимости транзакций. Модель предполагает один publisher с poll 2 с; локальный этап 4 запускает два publisher с poll 200 мс и дополнительные scheduler jobs, поэтому точное число SQL-вызовов стенда из этой оценки не выводится. Реализация и фактический смешанный профиль описаны в stage4_verification.md; пересчёт фонового SQL и retention входит в эксплуатационную доработку.", "",
           f'{number(r["events"], 0)} событий = {number(r["notifications"], 0)} состояний заказов + {number(2 * r["created"] * model["commerce"]["items_per_order"] + model["commerce"]["stock_updates_day"], 0)} изменений запаса + {number(model["commerce"]["catalog_updates_day"], 0)} изменений каталога. В отличие от первоначального укрупнения, InventoryChanged считается на каждый SKU. Подтверждения/повторные доставки брокера считаются сверх публикаций. Redis: модель rate-limit плюс кэш карточек; Lua/HASH операций прототипа может быть больше логической GET/SET операции. Резервы, цены и корзины не полагаются на сохранность кэша.', "",
           f'Для бюджета служебных запросов взяты {p["api_instances_budget"]} API-экземпляров и {p["lb_instances"]} LB: readiness каждые {p["health_interval_seconds"]} с даёт **{number(p["api_instances_budget"] * p["lb_instances"] / p["health_interval_seconds"])} проверок/с** вне пользовательского RPS. Число экземпляров — пример для бюджета health, не окончательный sizing.', "",
           "## Данные на пользователя и логическое хранение первого года", "",
           "Среднее по MAU ниже — распределённая доля общего хранения, а не персональная квота. Профили делятся на 1 млн MAU при базе 2 млн учётных записей. Общие каталог, картинки и эксплуатационные данные явно отделены от принадлежащих пользователю объектов. Для среднего по зарегистрированному пользователю профиль равен 1 записи и 0,000002 ГБ.", "",
           table(["Категория", "Записей/объектов", "Логически, ГБ", "Штук на MAU", "ГБ на MAU"], [[x["name"], number(x["count"], 0), number(x["logical"] / GB, 4), number(x["count"] / a["mau"], 4), number(x["logical"] / GB / a["mau"], 9)] for x in one]), "",
           "Логи и спаны показаны после допущенного сжатия; поисковые документы — до индекса; кэш — до overhead. Для очереди приведён backlog шести часов остановки, а не все опубликованные за год сообщения. Для историй/платежей в пределах горизонта сохраняется вся история, для служебных данных — заданное окно.", "",
           "## Хранение по системам и рост", "",
           "Год 1 использует исходные объёмы. На границе каждого следующего года аудитория/операции растут на 30%, каталог на 20%; внутри года интенсивность постоянна. История заказов суммируется за годы, временные данные рассчитываются по нагрузке текущего года. Прогноз не является рыночным обещанием роста.", "",
           table(["Категория", "Штук, год 1", "Логически, ТБ, год 1", "С копиями/overhead, ТБ, год 1", "С копиями/overhead, ТБ, год 3"], [[x["name"], number(x["count"], 0), number(x["logical"] / TB, 6), number(x["physical"] / TB, 6), number(y["physical"] / TB, 6)] for x, y in zip(one, three)]), "",
           "PostgreSQL: логика ×2 на индексы/версии ×3 копии. OpenSearch: документ ×2 на индекс ×2 копии. Redis: значения ×1,5 overhead ×2 копии. Очередь: backlog ×1,2 overhead ×3 копии. Логи/спаны: сжатие ×2 копии; метрики: 2 байта/точку ×1,3 overhead, 1 копия. Все множители — начальные допущения для уточнения в чекпоинте 2, не гарантии продукта.", "",
           "Объектное хранилище показано в логической доступной ёмкости: внутренние копии управляемого S3 не умножаются произвольно. При самостоятельной репликации Garage добавляется фактический replication factor и свободное место. Старые версии медиа не накапливаются бессрочно; модель считает живые объекты и два превью, очистку незавершённых/устаревших загрузок проектируем отдельно.", "",
           table(["Итог", "Год 1, ТБ", "Год 3, ТБ"], [
               ["Данные с указанными копиями/overhead", number(sum(x["physical"] for x in one) / TB, 4), number(sum(x["physical"] for x in three) / TB, 4)],
               ["Отдельный бюджет бэкапов и WAL", number(backup1 / TB, 4), number(backup3 / TB, 4)],
               ["Всего без свободного резерва дисков", number((sum(x["physical"] for x in one) + backup1) / TB, 4), number((sum(x["physical"] for x in three) + backup3) / TB, 4)],
           ]), "",
           "Бюджет бэкапов: две полные SQL-копии после сжатия ×0,5 + одна полная копия медиа + WAL за 7 суток по 100 КБ/созданный заказ. Это отдельное допущение, которое не выводится из реального объёма WAL. Свободное место под compaction, restore и рост сверх модели выбирается в чекпоинте 2. 100 ГБ потребительского Облака Mail — ресурс малого стенда, а не ёмкость для полного бэкапа целевой системы.", "",
           "## Распродажа и чувствительность", "",
           f'F01: {p["flash_attempts"]} уникальных checkout за {p["flash_seconds"]} с = {number(p["flash_attempts"] / p["flash_seconds"])} запросов/с к одному SKU. Дополнительные повторы {number(p["flash_repeat_fraction"] * 100, 0)}% дают {number(p["flash_attempts"] * (1 + p["flash_repeat_fraction"]) / p["flash_seconds"])} запросов/с. В контролируемом прогоне ожидаются {p["flash_stock"]} резервов и {p["flash_attempts"] - p["flash_stock"]} бизнес-отказов; повторы не создают новых резервов. Этот всплеск прибавляется к обычному API-пику, а не умножает весь трафик на 100.', "",
           f'F02: тестируем {number(p["late_paid_fraction"] * 100, 0)}% поздних оплат как долю успешных резервов F01 (то есть {number(p["flash_stock"] * p["late_paid_fraction"], 0)} из {p["flash_stock"]}) и конкурентное истечение; компенсация учитывается отдельно от обычной платёжной конверсии.', "",
           table(["Сценарий", "API пик, RPS", "Картинки на origin, пик RPS", "Объяснение"], [
               [f"Общий пик ×{f}", number(r["requests"] / DAY * f), number(r["image_requests"] * (1 - media["cdn_hit"]) / DAY * f), "Одинаковый профиль операций"] for f in (5, 10, 30)
           ] + [["Пик ×10 + F01", number(r["requests"] / DAY * factor + p["flash_attempts"] * (1 + p["flash_repeat_fraction"]) / p["flash_seconds"]), number(r["image_requests"] * (1 - media["cdn_hit"]) / DAY * factor), "Дополнительные checkout; медиа неизменно"]]), "",
           table(["CDN hit ratio", "Origin пик, RPS", "Origin, ГБ/сутки"], [[number(h * 100, 0) + "%", number(r["image_requests"] * (1 - h) / DAY * factor), number(r["image_bytes"] * (1 - h) / GB)] for h in (0.99, 0.95, 0.80, 0)]), "",
           "Без CDN весь медиа-поток приходит на origin. На одной ВМ HTTP-кэш может сократить обращения к диску/Garage, но исходящий сетевой поток ВМ остаётся полным. При Redis hit 0% все карточки идут в PostgreSQL, а не увеличивают число пользовательских запросов; для защиты БД необходимы лимиты конкуренции и admission control.", ""]
    summary = "\n".join([
        "Все значения ниже — результаты модели с допущениями, а не замеры стенда. Подробные таблицы, формулы и чувствительность: [расчёт](docs/capacity.md); исходные числа: [capacity.json](model/capacity.json).", "",
        table(["Показатель", "Результат"], [
            ["MAU / DAU / зарегистрировано", f'{number(a["mau"], 0)} / {number(a["dau"], 0)} / {number(a["registered_users"], 0)}'],
            ["Попытки / созданные / оплаченные заказы в сутки", f'{number(r["base"]["OP06"], 0)} / {number(r["created"], 0)} / {number(r["paid"], 0)}'],
            ["API в среднем / пик ×10", f'{number(r["requests"] / DAY)} / {number(r["requests"] / DAY * factor)} RPS'],
            ["Медиа через CDN в среднем / пик", f'{number(r["image_requests"] / DAY)} / {number(r["image_requests"] / DAY * factor)} RPS'],
            ["Медиа на origin при hit 95%, пик", f'{number(r["image_requests"] * (1 - media["cdn_hit"]) / DAY * factor)} RPS'],
            ["Вход / выход API", f'{number(r["ingress"] / GB)} / {number(r["egress"] / GB)} ГБ/сутки'],
            ["Выход медиа CDN / origin", f'{number(r["image_bytes"] / GB)} / {number(r["image_bytes"] * (1 - media["cdn_hit"]) / GB)} ГБ/сутки'],
            ["Пиковый вход / выход API", f'{number(r["ingress"] * 8 / DAY / 10**6 * factor)} / {number(r["egress"] * 8 / DAY / 10**6 * factor)} Мбит/с'],
            ["Пиковый выход медиа CDN / origin", f'{number(r["image_bytes"] * 8 / DAY / 10**6 * factor)} / {number(r["image_bytes"] * (1 - media["cdn_hit"]) * 8 / DAY / 10**6 * factor)} Мбит/с'],
            ["Вход image GET CDN / origin", f'{number(r["image_ingress"] / GB)} / {number(r["image_ingress"] * (1 - media["cdn_hit"]) / GB)} ГБ/сутки'],
            ["Данные с копиями/overhead, год 1 / год 3", f'{number(sum(x["physical"] for x in one) / TB, 4)} / {number(sum(x["physical"] for x in three) / TB, 4)} ТБ'],
            ["Бэкапы отдельно, год 1 / год 3", f'{number(backup1 / TB, 4)} / {number(backup3 / TB, 4)} ТБ'],
        ]), "",
        "Частоты действий относятся к одному DAU; для служебных и платёжных запросов частота выводится из сценария. В RPS уже включены повторы.", "",
        table(["Операция", "Действий/DAU/сутки", "Запросов/сутки с повторами", "Средний RPS", "Пик RPS"], [[x["id"] + " " + x["name"], number(x["actions"]) if "actions" in x else "По сценарию", number(x["daily"]), number(x["daily"] / DAY), number(x["daily"] / DAY * factor)] for x in r["rows"]]), "",
        "Продуктовые данные на MAU за первый год — распределённая доля общего хранения, а не персональная квота. Общие данные помечены отдельно; профиль на одного зарегистрированного пользователя — одна запись по 2 КБ.", "",
        table(["Категория", "Штук на MAU", "ГБ на MAU, логически"], [[x["name"] + (" (общие)" if x["name"] in {"Карточки", "Исходные изображения", "Превью двух размеров"} else ""), number(x["count"] / a["mau"], 4), number(x["logical"] / GB / a["mau"], 9)] for x in one if x["name"] in {"Профили", "Карточки", "Корзины", "Позиции корзин", "Заказы", "Позиции заказов", "Платежи", "Исходные изображения", "Превью двух размеров"}]), "",
        "Хранение по системам включает указанные в модели индексы/overhead и копии; внутренние копии управляемого S3 не прибавляются. Подробные числа записей/объектов и retention каждой категории доступны в расчёте.", "",
        table(["Система/категория", "Логических записей/объектов, год 1", "С копиями/overhead, ТБ, год 1", "ТБ, год 3"], [[name, number(sum(x["count"] for x in one if x["family"] in families), 0), number(sum(x["physical"] for x in one if x["family"] in families) / TB, 6), number(sum(x["physical"] for x in three if x["family"] in families) / TB, 6)] for name, families in [("PostgreSQL", {"sql"}), ("OpenSearch", {"search"}), ("Redis", {"cache"}), ("Медиа, исходники и превью", {"media"}), ("Брокер, backlog 6 часов", {"queue"}), ("Логи и спаны, после сжатия", {"logs", "traces"}), ("Метрики", {"metrics"})]]), "",
        "Объём SQL включает корзины, историю, платежи, резервы, идемпотентность, outbox/inbox и дедупликацию webhook. Для копий БД принят множитель 3, для индексов/версий — 2; физические конфигурации и коэффициенты уточним в чекпоинте 2.", "",
        "География России в модели использует доли оборота из исследования АКИТ как proxy долей пользователей. Это допущение; фактическую аудиторию нашего магазина эти доли не измеряют.", "",
        table(["Регион", "Доля модели", "MAU"], [[x["name"], number(x["share"] * 100, 1) + "%", number(a["mau"] * x["share"], 0)] for x in model["regions"]]), "",
        f'F01: {p["flash_attempts"]} уникальных checkout за {p["flash_seconds"]} с плюс {number(p["flash_repeat_fraction"] * 100, 0)}% повторов = дополнительные {number(p["flash_attempts"] * (1 + p["flash_repeat_fraction"]) / p["flash_seconds"], 0)} RPS к одному SKU поверх обычного пика; запаса хватает на {p["flash_stock"]} заказов. Профиль ×30 и отсутствие CDN рассматриваются отдельно, чтобы не скрывать чувствительность результата.',
    ])
    return "\n".join(out), summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write and args.check:
        parser.error("Choose --write or --check")
    model = json.loads((ROOT / "model/capacity.json").read_text())
    full, summary = report(model)
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text()
    start, end = "<!-- capacity:start -->", "<!-- capacity:end -->"
    assert readme.count(start) == readme.count(end) == 1, "Missing/duplicate README capacity markers"
    before, remainder = readme.split(start)
    _, after = remainder.split(end)
    updated = before + start + "\n" + summary + "\n" + end + after
    target = ROOT / "docs/capacity.md"
    if args.write:
        target.write_text(full)
        readme_path.write_text(updated)
        print("Updated docs/capacity.md and README capacity summary")
    elif args.check:
        if not target.exists() or target.read_text() != full or readme != updated:
            raise SystemExit("Generated tables differ; run with --write")
        print("Capacity model invariants and generated tables are consistent")
    else:
        print(full)


if __name__ == "__main__":
    main()
