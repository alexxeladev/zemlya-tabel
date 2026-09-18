"""
Выгрузка табеля вахты в Excel (task_vahta).

Формат снят с образца заказчика `Табель зп блок охрана август.xlsx`: шапка с
юрлицом и отчётным периодом, колонки № · ФИО · Должность · Смена/руб · КП ·
сетка дней · Кол-во смен · Зарплата · Трудоустройство · Премия · Штраф ·
Оф. выплата · К выплате · Примечания · юрлица · Итого разбивка · Налоги.

«Итого разбивка» — база разнесения: начислено ПЛЮС налог на официальную часть
(task_vahta_taxes); хвостовая «Налоги» объясняет, почему она больше начисленного.

Отличие от образца **одно и согласовано**: у заказчика два листа по половинам
месяца (1–15 и 16–31), здесь — ОДИН лист на месяц с полной сеткой 1–31 и чертой
между половинами, как на экране. Суммы половин при этом не теряются: строка
итогов внизу печатает их отдельно.

Экспортёр НИЧЕГО НЕ СЧИТАЕТ — все суммы приходят посчитанными в `GuardMonthRead`
(`services/guard_month.py`). Вторая формула здесь однажды разошлась бы с экраном.
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.schemas.guard import GuardCardRead, GuardMonthRead
from app.services.company_order import company_display_name, company_order_by

_ZERO = Decimal("0")

_MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

#: Колонки до сетки дней — в порядке образца.
_LEAD_HEADERS = ["№", "ФИО", "Должность", "Смена/руб", "КП"]
#: Колонки после сетки дней, до колонок юрлиц.
_TAIL_HEADERS = [
    "Кол-во смен", "Зарплата", "Трудоустройство", "Премия", "Штраф",
    "Оф. выплата", "К выплате", "Примечания",
]


def _thin() -> Border:
    s = Side(style="thin", color="000000")
    return Border(left=s, right=s, top=s, bottom=s)


def _half_border() -> Border:
    """Черта между расчётными половинами — жирная левая граница дня 16."""
    thin = Side(style="thin", color="000000")
    return Border(
        left=Side(style="medium", color="000000"), right=thin, top=thin, bottom=thin
    )


def _set(ws, row, col, value=None, *, bold=False, center=True, fill=None,
         border=True, wrap=False, size=9):
    cell = ws.cell(row=row, column=col)
    if value is not None:
        cell.value = value
    cell.font = Font(name="Arial", size=size, bold=bold)
    cell.alignment = Alignment(
        horizontal="center" if center else "left", vertical="center", wrap_text=wrap
    )
    if border:
        cell.border = _thin()
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
    return cell


def _money(value: Decimal | None) -> float:
    return float(value or _ZERO)


def generate_guard_timesheet_excel(db: Session, month: GuardMonthRead) -> bytes:
    """Собрать .xlsx табеля вахты за месяц. Возвращает содержимое файла."""
    companies = (
        db.query(Company)
        .filter(Company.is_active == True)  # noqa: E712
        .order_by(*company_order_by())
        .all()
    )
    company_ids = [c.id for c in companies]

    wb = Workbook()
    ws = wb.active
    ws.title = f"Вахта {month.month:02d}.{month.year}"

    days = list(range(1, month.days_in_month + 1))
    col_days_start = len(_LEAD_HEADERS) + 2  # +1 — отступ в колонке A, как в образце
    col_tail_start = col_days_start + len(days)
    col_companies_start = col_tail_start + len(_TAIL_HEADERS)
    col_total = col_companies_start + len(company_ids)
    # «Налоги» — хвостом ПОСЛЕ «Итого разбивка» (task_vahta_taxes): разбивка
    # включает налог на официальную часть и больше «Зарплата + премия − штраф»
    # ровно на эту колонку. В середину не вставлять — порядок колонок из образца.
    col_tax = col_total + 1
    total_cols = col_tax

    # ── Шапка ────────────────────────────────────────────────────────────────
    ws.cell(row=1, column=2).value = "ВАХТА — табель охраны"
    ws.cell(row=1, column=2).font = Font(name="Arial", size=12, bold=True)
    ws.cell(row=2, column=2).value = (
        f"Отчётный период: {_MONTHS_RU[month.month]} {month.year} "
        f"(расчётные половины 1–{month.first_half_last_day} и "
        f"{month.first_half_last_day + 1}–{month.days_in_month})"
    )
    ws.cell(row=2, column=2).font = Font(name="Arial", size=9)

    header_row = 4
    for i, title in enumerate(_LEAD_HEADERS):
        _set(ws, header_row, 2 + i, title, bold=True, fill="FFE8EEF7", wrap=True)
    for i, day in enumerate(days):
        cell = _set(ws, header_row, col_days_start + i, day, bold=True, fill="FFE8EEF7")
        if day == month.first_half_last_day + 1:
            cell.border = _half_border()
    for i, title in enumerate(_TAIL_HEADERS):
        _set(ws, header_row, col_tail_start + i, title, bold=True, fill="FFE8EEF7", wrap=True)
    for i, company in enumerate(companies):
        cell = _set(
            ws, header_row, col_companies_start + i,
            company_display_name(company), bold=True, fill="FFEFF7EE", wrap=True,
        )
        cell.comment = None
    _set(ws, header_row, col_total, "Итого разбивка", bold=True, fill="FFEFF7EE", wrap=True)
    _set(ws, header_row, col_tax, "Налоги (входят в разбивку)", bold=True,
         fill="FFEFF7EE", wrap=True)

    row = header_row + 1

    for zone in month.zones:
        # Полоса зоны обслуживания — верхний уровень группировки, как колонка A
        # в табеле заказчика.
        _set(ws, row, 2, f"ЗОНА · {zone.zone_name}", bold=True, center=False,
             fill="FFB8CCE4")
        for col in range(3, total_cols + 1):
            _set(ws, row, col, fill="FFB8CCE4")
        row += 1
        row = _write_cards(ws, row, zone.cards, month, days, col_days_start,
                           col_tail_start, col_companies_start, col_total,
                           company_ids, total_cols)

    return _finish(ws, wb, month, days, col_days_start, col_tail_start,
                   col_companies_start, col_total, company_ids, total_cols, row,
                   header_row)


def _write_cards(ws, row, cards: list[GuardCardRead], month, days, col_days_start,
                 col_tail_start, col_companies_start, col_total, company_ids,
                 total_cols):
    """Карточки внутри зоны: сначала экипажи ГБР, потом объекты с постами."""
    for card in cards:
        # Заголовок карточки отдельной строкой: в образце его роль играет
        # колонка «КП» первой строки, но при одном листе на месяц полоса
        # читается лучше. Экипаж ГБР помечен явно — это выездная бригада, а не
        # охраняемый объект.
        title = (
            f"ЭКИПАЖ ГБР · {card.name}" if card.kind == "crew" else f"ОБЪЕКТ · {card.name}"
        )
        _set(ws, row, 2, title, bold=True, center=False, fill="FFDCE6F1")
        for col in range(3, total_cols + 1):
            _set(ws, row, col, fill="FFDCE6F1")
        if card.objects:
            label = "Обслуживает: " if card.kind == "crew" else "Посты: "
            ws.cell(row=row, column=3).value = label + " · ".join(card.objects)
            ws.cell(row=row, column=3).font = Font(name="Arial", size=8, italic=True)
            ws.cell(row=row, column=3).alignment = Alignment(horizontal="left")
        row += 1

        for number, item in enumerate(card.rows, start=1):
            _set(ws, row, 2, number)
            # Пустой слот: человека нет — так в образце и есть, не падаем.
            _set(ws, row, 3, item.employee_name or "— вакансия —", center=False)
            _set(ws, row, 4, item.job_title_name, center=False)
            _set(ws, row, 5, _money(item.rate))
            # «КП» — где человек работает: у экипажа его имя, у поста
            # «Объект · Пост» (в образце это «GW 1 GW 2» под шапкой объекта).
            _set(
                ws, row, 6,
                item.post_name if card.kind == "crew"
                else f"{item.site_name} · {item.post_name}"
                if item.site_name else item.post_name,
                center=False,
            )

            marked = set(item.days)
            for i, day in enumerate(days):
                cell = _set(
                    ws, row, col_days_start + i,
                    1 if day in marked else None,
                    fill="FFE2F4E8" if day in marked else None,
                )
                if day == month.first_half_last_day + 1:
                    cell.border = _half_border()

            _set(ws, row, col_tail_start, item.shifts)
            _set(ws, row, col_tail_start + 1, _money(item.salary))
            _set(ws, row, col_tail_start + 2, "Официальный" if item.is_official else None)
            _set(ws, row, col_tail_start + 3, _money(item.premium))
            _set(ws, row, col_tail_start + 4, _money(item.penalty))
            _set(ws, row, col_tail_start + 5, _money(item.official_payout))
            _set(ws, row, col_tail_start + 6, _money(item.net_payout), bold=True)
            _set(ws, row, col_tail_start + 7, item.note, center=False)

            distribution = item.distribution or {}
            for i, cid in enumerate(company_ids):
                # Юрлицо без доли пишем НУЛЁМ, а не пустой ячейкой: так в
                # образце — по строке видно, что оно в распределение не вошло.
                _set(ws, row, col_companies_start + i, _money(distribution.get(cid)))
            # «Итого разбивка» — база разнесения: начислено + налог.
            _set(ws, row, col_total, _money(item.distribution_base), bold=True)
            _set(ws, row, col_total + 1, _money(item.tax))
            row += 1
    return row


def _finish(ws, wb, month, days, col_days_start, col_tail_start,
            col_companies_start, col_total, company_ids, total_cols, row,
            header_row):
    """Итоги, суммы половин и ширины колонок."""
    # ── Итоги ────────────────────────────────────────────────────────────────
    row += 1
    _set(ws, row, 2, "ИТОГО", bold=True, center=False, fill="FFF2F2F2")
    for col in range(3, total_cols + 1):
        _set(ws, row, col, fill="FFF2F2F2")
    _set(ws, row, col_tail_start, month.total_shifts, bold=True, fill="FFF2F2F2")
    _set(ws, row, col_tail_start + 6, _money(month.total_net_payout), bold=True, fill="FFF2F2F2")

    company_totals = {t.company_id: t.amount for t in month.company_totals}
    for i, cid in enumerate(company_ids):
        _set(
            ws, row, col_companies_start + i,
            _money(company_totals.get(cid)), bold=True, fill="FFF2F2F2",
        )
    _set(
        ws, row, col_total,
        _money(month.total_distribution_base),
        bold=True, fill="FFF2F2F2",
    )
    _set(ws, row, col_total + 1, _money(month.total_tax), bold=True, fill="FFF2F2F2")
    row += 2

    # Суммы по каждой расчётной половине — то, ради чего в образце два листа.
    for half in month.halves:
        first = 1 if half.half == 1 else month.first_half_last_day + 1
        last = month.first_half_last_day if half.half == 1 else month.days_in_month
        ws.cell(row=row, column=2).value = (
            f"Половина {half.half} ({first}–{last}): смен {half.shifts}, "
            f"начислено {_money(half.accrued):.2f}, "
            f"к выплате {_money(half.net_payout):.2f}, "
            f"налоги {_money(half.tax):.2f}"
        )
        ws.cell(row=row, column=2).font = Font(name="Arial", size=9, bold=True)
        row += 1

    # ── Ширины ───────────────────────────────────────────────────────────────
    ws.column_dimensions["A"].width = 2
    ws.column_dimensions["B"].width = 5
    ws.column_dimensions["C"].width = 26
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 11
    ws.column_dimensions["F"].width = 22
    for i in range(len(days)):
        ws.column_dimensions[get_column_letter(col_days_start + i)].width = 3.5
    for i in range(len(_TAIL_HEADERS)):
        ws.column_dimensions[get_column_letter(col_tail_start + i)].width = 13
    for i in range(len(company_ids)):
        ws.column_dimensions[get_column_letter(col_companies_start + i)].width = 14
    ws.column_dimensions[get_column_letter(col_total)].width = 15
    ws.column_dimensions[get_column_letter(col_total + 1)].width = 14
    ws.freeze_panes = ws.cell(row=header_row + 1, column=col_days_start)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
