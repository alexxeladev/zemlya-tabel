"""
Порядок перечисления юрлиц и их отображаемые названия — ЕДИНСТВЕННЫЙ источник
правды (task_vedomost_format ч.1 и ч.2).

Порядок задаётся админом в справочнике (`companies.sort_order`) и обязан быть
ОДИНАКОВЫМ везде: колонки и чипы табеля, блок «по компаниям», ведомость и её
Excel, Т-13, дашборд, фильтры. Поэтому сортировка живёт здесь и нигде больше —
`order_by(Company.id)` / `sorted(company_ids)` по месту неминуемо разъехались бы
с настройкой, и цвет компании (он берётся по индексу в списке) поехал бы вместе
с ними.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

from app.models.companies import Company

# Правовая форма в начале названия — в узкую колонку не влезает и ничего не
# говорит. Та же нормализация по смыслу, что `companyLabel` во фронте.
_LEGAL_FORM = re.compile(r"^(ООО|ОАО|ЗАО|ПАО|АО|ИП|НАО)\s+", re.IGNORECASE)
_QUOTES = "«»\"'“”„ "


def company_order_by() -> tuple:
    """ORDER BY для запросов к companies. Второй ключ — id: он разводит
    компании с одинаковым sort_order (после массовой правки такое штатно)."""
    return (Company.sort_order, Company.id)


def company_sort_key(company: Company) -> tuple[int, int]:
    return (company.sort_order or 0, company.id)


def sort_companies(companies: Iterable[Company]) -> list[Company]:
    return sorted(companies, key=company_sort_key)


def sort_company_ids(
    company_ids: Iterable[int | None],
    companies_by_id: Mapping[int, Company],
) -> list[int | None]:
    """Отсортировать id юрлиц тем же порядком.

    None («нет компании») уходит в конец, неизвестный id — тоже: справочник
    мог не отдать неактивную компанию, а строку с её часами терять нельзя.
    """
    def key(cid: int | None) -> tuple[int, int, int]:
        if cid is None:
            return (2, 0, 0)
        company = companies_by_id.get(cid)
        if company is None:
            return (1, 0, cid)
        return (0, company.sort_order or 0, company.id)

    return sorted(company_ids, key=key)


def order_index(ordered_company_ids: Iterable[int]) -> dict[int, int]:
    """«id юрлица → его место в настроенном порядке».

    Для мест, где под рукой не сами компании, а только их id (итоги дашборда,
    доп. юрлица сотрудника): список приходит из запроса, уже отсортированного
    `company_order_by()`, и его порядок здесь просто фиксируется числами.
    """
    return {cid: i for i, cid in enumerate(ordered_company_ids)}


def company_display_name(company: Company | None) -> str:
    """Короткое название для колонок и подписей.

    Приоритет: заданное вручную `short_name` → название без правовой формы и
    кавычек → код. Код остаётся ключом (в API, в БД, в узких местах вроде
    выпадашки внутри ячейки дня), здесь — только показ.
    """
    if company is None:
        return ""
    manual = (company.short_name or "").strip()
    if manual:
        return manual
    name = (company.name or "").strip()
    if not name:
        return company.code
    short = _LEGAL_FORM.sub("", name).strip(_QUOTES).strip()
    return short or name
