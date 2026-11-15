# Запуск транзакционного backend

Реализованы Spring Boot 3.5.16, Java 25, Spring MVC/JDBC/Security, Flyway, PostgreSQL 17 и Redis 7.4. HTTP-стенд доступен только на loopback, использует синтетические Basic-учётные записи demo-profile и не предназначен для публичного эквайринга. Пароли в demo-seed/Compose являются тестовыми фикстурами, а не production secrets.

## Запуск

Из корня репозитория:

```bash
docker compose -f infra/stage2/compose.yaml up -d --build
curl http://127.0.0.1:18080/readyz
curl http://127.0.0.1:18080/api/products/1
```

Два API находятся за HAProxy на порту 18080, прямые loopback-порты — 18081 и 18082. PostgreSQL/Redis не публикуют порты на хосте. Первый старт применяет миграции и создаёт 100 тыс. товаров, 1000 покупателей и администратора. Повторный старт использует сохранённый Docker volume. Стенд этапа 1 также использует 18080, поэтому одновременно эти два примера не запускаются.

```bash
curl -u buyer-1:demo-buyer-1 -X PUT \
  -H 'Content-Type: application/json' \
  -d '{"quantity":1}' \
  http://127.0.0.1:18080/api/cart/items/1

curl -u buyer-1:demo-buyer-1 http://127.0.0.1:18080/api/cart
```

Для исходного товара 1 цена — 10100 копеек. Перенесите актуальные version и totalMinor корзины в запрос оформления:

```bash
curl -u buyer-1:demo-buyer-1 -X POST \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: example-order' \
  -d '{"expectedCartVersion":1,"expectedTotalMinor":10100}' \
  http://127.0.0.1:18080/api/orders
```

Пример предполагает новую корзину и отсутствие предшествующих изменений. Повтор с тем же ключом и телом возвращает тот же результат создания; текущее состояние заказа читается отдельным GET. Платёжный провайдер ещё не подключён, поэтому успешно созданный заказ ожидает оплаты до ручного запуска истечения или будущего обработчика.

## Операции

| Метод и путь | Доступ | Назначение |
| --- | --- | --- |
| GET `/livez`, `/readyz` | Публичный локальный | Живость процесса / доступ к основному SQL |
| GET `/api/products?categoryId=1&afterId=0&limit=20` | Публичный | Категория с keyset pagination, limit до 100 |
| GET `/api/products/{id}` | Публичный | Cache-aside карточки |
| GET `/api/cart` | Покупатель | Только собственная корзина, версия, актуальная сумма |
| PUT `/api/cart/items/{id}` | Покупатель | Количество 0–100; 0 удаляет; до 50 разных SKU |
| POST `/api/orders` | Покупатель | Атомарное оформление и резерв всех позиций |
| GET `/api/orders` | Покупатель | Собственная история, 20 записей; курсор beforeCreatedAt + beforeId |
| GET `/api/orders/{id}` | Покупатель | Только свой заказ и снимки позиций |
| PUT `/api/admin/products/{id}` | Администратор | name, priceMinor, active; изменение версии и invalidation |
| PUT `/api/admin/inventory/{id}` | Администратор | onHand; не может стать меньше reserved |
| POST `/api/admin/reservations/expire` | Администратор | До 100 истёкших заказов за транзакцию; повтор безопасен |
| GET `/api/admin/cache-stats` | Администратор | Origin-loads, hits, ошибки Redis для проверки |
| GET `/actuator/metrics` | Администратор | Метрики JVM/HTTP/Hikari, основа следующего этапа |

Учётные записи: buyer-N / demo-buyer-N, где N от 1 до 1000; admin / demo-admin. Идентификатор владельца берётся из проверенной учётной записи, а не из тела запроса. У другого покупателя чужой заказ возвращает 404. Поиск, загрузка изображений, реальная оплата, автоматический expiry worker и доставка outbox остаются следующими частями реализации.

Бизнес-отказы: OUT_OF_STOCK, PRICE_CHANGED, CART_CHANGED, EMPTY_CART, IDEMPOTENCY_CONFLICT и STOCK_BELOW_RESERVED. Они не смешиваются с инфраструктурным 503 DATABASE_BUSY/CATALOG_BUSY. Повтор бизнес-отказа с прежним ключом сохраняет прежний результат в пределах окна; после изменения условий используется новый ключ и актуальная версия корзины.

## Воспроизводимая проверка

```bash
python3 scripts/check_stage2.py
```

Команда создаёт собственный Compose-проект со случайным суффиксом, выполняет проверку, dump/restore и затем удаляет только этот тестовый проект с его временным volume. Ручной постоянный стенд нужно предварительно остановить, чтобы освободить loopback-порты. Результат сохраняется в `artifacts/stage2/results.json`, планы — в `docs/stage2_query_plans.json`. Большие бинарные дампы в Git не сохраняются.

Остановка ручного стенда с сохранением БД:

```bash
docker compose -f infra/stage2/compose.yaml down
```

Без demo-profile создаётся только схема, а тестовые пользователи/пароли не загружаются. Production-подключение требует отдельных учётных данных, защищённого транспорта и выбранного способа регистрации/аутентификации. Один Docker-хост не доказывает отказоустойчивость к потере AZ.
