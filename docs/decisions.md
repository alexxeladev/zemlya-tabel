# Архитектурные решения

## Контекст проекта
Система учёта рабочего времени для девелоперской группы «Земля МО».
Несколько юрлиц, между которыми распределяется зарплата сотрудника.

## Стек
- Backend: Python 3.11+, FastAPI, SQLAlchemy 2.x, PostgreSQL, Alembic
- Frontend: React (будет позже)
- Хостинг: on-premise, Ubuntu Server

## Роли
- Admin: всё
- Manager (Руководитель): видит только свой department
- Accountant (Бухгалтер): видит все departments, закрывает периоды, выгружает в 1С
- Employee (Сотрудник): видит только свои часы

## Workflow периода
Draft → Pending Review → Closed
После Closed правки только Admin, обязательный комментарий, audit log.

## Auth
На старте: ручное создание учёток админом + временный пароль, смена при первом входе. JWT в HTTP-Only куки.
В будущем: LDAP/SSO Yandex. Auth-слой отдельный, чтобы заменить провайдер.

## Audit log
Append-only таблица, поля: who, when, entity_type, entity_id, action, before, after, reason.
Логируем: CRUD всех сущностей, смены статуса периодов, правки часов.

## Расчёт ЗП
Оклад × (отработано / норма часов), переработка × 1.5, праздничные × 1.5.

## Изоляция данных
Employee принадлежит Department. Manager видит только свой Department.

## Экспорт в 1С
Этап 1: XML-файл, бухгалтер загружает вручную.
Этап 2: REST API в 1С (HTTP-сервис, опубликованный с 1С-стороны).

## Soft delete
Пользователи не удаляются физически — `is_active=False`. Причина: audit_log ссылается на `users.id` через FK; физическое удаление сломает историю.
Принцип применяется ко всем сущностям, которые участвуют в audit_log.

## Audit log
Append-only — нет `updated_at`, нет UPDATE. Все правки через INSERT новой записи.
JSONB-поля `before`/`after` хранят снимок объекта до и после изменения.
Индексы по `(entity_type, entity_id)`, `actor_id`, `created_at` для быстрой фильтрации в UI аудита.
