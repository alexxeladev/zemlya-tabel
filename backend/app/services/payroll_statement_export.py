"""
Excel-выгрузка сводной ведомости «Расчёт ЗП» (задача 3.11b п.3).

Структура по образцу финдиректора (лист «Подразделение»): инфо-колонки сотрудника,
оклад/норма/факт/переработка/начисления, Итого начислено, удержания, К выплате,
далее колонки распределения по каждому юрлицу (Итого начислено × %). Деньги —
числами с 2 знаками. Отпуск/больничный (J,K,R образца) пока не выводятся.
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.schemas.payroll_statement import PayrollStatementRead

_ORG_NAME = "ДЕВЕЛОПМЕНТ ГРУППА «ЗЕМЛЯ МО»"

# Откуда взято распределение по юрлицам (каскад task_distribution_v2 ч.3)
_SOURCE_LABELS = {
    "month": "проценты переопределены на месяц",
    "employee": "проценты из карточки сотрудника",
    "department": "дефолт отдела",
    "hours": "распределено по часам (авто)",
}

_MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _border() -> Border:
    s = Side(style="thin", color="000000")
    return Border(left=s, right=s, top=s, bottom=s)


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(value))


def generate_statement_excel(statement: PayrollStatementRead) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Подразделение"

    border = _border()
    bold = Font(name="Arial", size=9, bold=True)
    normal = Font(name="Arial", size=9, bold=False)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    title_fill = PatternFill("solid", fgColor="FFD9E1F2")
    total_fill = PatternFill("solid", fgColor="FFF2F2F2")
    warn_fill = PatternFill("solid", fgColor="FFFFE5E5")

    companies = statement.companies
    # Базовые колонки (без распределения)
    base_headers = [
        "№", "Таб.№", "ФИО", "Компания", "Подразделение", "Должность",
        "Режим работы", "Оклад / ставка", "Норма час", "Факт час", "Коэф. пер.",
        "Кол-во пер.", "Сумма пер.", "Начислено оклад", "Премия", "KPI",
        "Премия доп.", "Итого начислено", "Аванс/Удерж.", "К выплате",
    ]
    dist_headers = [f"{c.code}\n{c.name}" for c in companies]
    # Обоснования (task_ux_improvements ч.1b) — ХВОСТОВЫМИ колонками, чтобы не
    # сдвигать блок формы финдира: в шаблоне у него зафиксированы буквы колонок.
    reason_headers = [
        "Обоснование премии", "Обоснование KPI", "Обоснование удержаний",
    ]
    headers = (
        base_headers + dist_headers
        + ["Итого распред."] + reason_headers + ["Примечание"]
    )
    n_cols = len(headers)

    # ── Шапка ─────────────────────────────────────────────────────────────────
    ws.cell(row=1, column=1, value=_ORG_NAME).font = Font(name="Arial", size=12, bold=True)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(n_cols, 8))
    period = f"Расчёт ЗП за {_MONTHS[statement.month - 1]} {statement.year}"
    ws.cell(row=2, column=1, value=period).font = bold
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=min(n_cols, 8))

    header_row = 4
    for col, title in enumerate(headers, start=1):
        c = ws.cell(row=header_row, column=col, value=title)
        c.font = bold
        c.alignment = center
        c.border = border
        c.fill = title_fill

    dist_start = len(base_headers) + 1  # 1-based колонка первой компании
    dist_total_col = dist_start + len(companies)
    premium_reason_col = dist_total_col + 1
    kpi_reason_col = premium_reason_col + 1
    deduction_reason_col = kpi_reason_col + 1
    note_col = deduction_reason_col + 1
    # Колонки премии / KPI / удержаний в base_headers — к ним же вешаем
    # обоснование примечанием ячейки, чтобы оно было видно рядом со значением.
    premium_col = base_headers.index("Премия") + 1
    kpi_col = base_headers.index("KPI") + 1
    deduction_col = base_headers.index("Аванс/Удерж.") + 1

    row = header_row + 1
    for i, r in enumerate(statement.rows, start=1):
        values = [
            i,
            r.tab_number or "",
            r.employee_name,
            r.main_company_name or "",
            r.department_name or "",
            r.position or "",
            r.schedule_name or "",
            # У посменного оклада нет — в колонку идёт ставка за смену
            # (условный оклад в ведомости не показываем, он служебный).
            _money(r.shift_rate if r.pay_type == "per_shift" else r.rate),
            _money(r.norm_hours),
            _money(r.fact_hours),
            _money(r.overtime_coefficient),
            _money(r.overtime_hours),
            _money(r.overtime_amount),
            _money(r.base_salary),
            _money(r.premium_amount),
            _money(r.kpi_amount),
            _money(r.premium_extra_amount),
            _money(r.accrued_total),
            _money(r.deductions),
            _money(r.net_payout),
        ]
        for col, val in enumerate(values, start=1):
            c = ws.cell(row=row, column=col, value=val)
            c.font = normal
            c.border = border
            c.alignment = left if col in (3, 4, 5, 6, 7) else center
            if isinstance(val, float):
                c.number_format = "#,##0.00"

        # Колонки распределения по компаниям
        amt_by_company = {d.company_id: d.amount for d in r.distribution}
        for j, comp in enumerate(companies):
            c = ws.cell(row=row, column=dist_start + j, value=_money(amt_by_company.get(comp.id)))
            c.font = normal
            c.border = border
            c.alignment = center
            c.number_format = "#,##0.00"
        # Итого распределения (контроль = Итого начислено)
        c = ws.cell(row=row, column=dist_total_col, value=_money(r.distribution_total))
        c.font = bold
        c.border = border
        c.alignment = center
        c.number_format = "#,##0.00"
        # Подсветка строки если ручная сумма процентов ≠ 100 (авто-доли не трогаем)
        if r.distribution and not r.is_auto_distributed and r.percent_sum != Decimal("100"):
            c.fill = warn_fill
        # ── Обоснования премий / KPI / удержаний (task_ux_improvements ч.1b) ──
        # Записей за месяц может быть несколько — каждая своей строкой в ячейке.
        deduction_reasons = list(r.advance_reasons)
        if r.loan_note:
            deduction_reasons.append(r.loan_note)
        for col, lines in (
            (premium_reason_col, r.premium_reasons),
            (kpi_reason_col, r.kpi_reasons),
            (deduction_reason_col, deduction_reasons),
        ):
            c = ws.cell(row=row, column=col, value="\n".join(lines) if lines else "")
            c.font = normal
            c.border = border
            c.alignment = left
        # То же самое примечанием ячейки рядом со значением — чтобы обоснование
        # было видно, не уезжая в хвост листа.
        for col, lines in (
            (premium_col, r.premium_reasons),
            (kpi_col, r.kpi_reasons),
            (deduction_col, deduction_reasons),
        ):
            if lines:
                ws.cell(row=row, column=col).comment = Comment(
                    "\n".join(lines), "Расчёт ЗП", height=len(lines) * 24 + 24, width=280,
                )

        # Примечание — тип оплаты и откуда взято распределение (каскад, ч.3)
        note = r.note or ""
        if r.pay_type == "per_shift":
            note = (
                (note + "; " if note else "")
                + f"посменно: {r.base_shifts} смен × ставку"
                + (
                    f" (+{r.worked_shifts - r.base_shifts} смен "
                    "в выходные/праздники по коэффициенту)"
                    if r.worked_shifts > r.base_shifts else ""
                )
            )
        source_label = _SOURCE_LABELS.get(r.distribution_source)
        if source_label and r.distribution:
            note = (note + "; " if note else "") + source_label
        c = ws.cell(row=row, column=note_col, value=note)
        c.font = normal
        c.border = border
        c.alignment = left
        row += 1

    # ── Итоговая строка ────────────────────────────────────────────────────────
    ws.cell(row=row, column=1, value="ИТОГО").font = bold
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    totals = {
        13: statement.total_overtime_amount,
        14: statement.total_base_salary,
        15: statement.total_premium,
        16: statement.total_kpi,
        18: statement.total_accrued,
        19: statement.total_deductions,
        20: statement.total_net_payout,
    }
    for col in range(1, n_cols + 1):
        c = ws.cell(row=row, column=col)
        c.border = border
        c.fill = total_fill
        if col in totals:
            c.value = _money(totals[col])
            c.font = bold
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.number_format = "#,##0.00"
    for j, comp in enumerate(companies):
        c = ws.cell(row=row, column=dist_start + j,
                    value=_money(statement.distribution_totals.get(comp.id, Decimal("0"))))
        c.font = bold
        c.border = border
        c.fill = total_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.number_format = "#,##0.00"
    grand_dist = sum(statement.distribution_totals.values(), Decimal("0"))
    c = ws.cell(row=row, column=dist_total_col, value=_money(grand_dist))
    c.font = bold
    c.border = border
    c.fill = total_fill
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.number_format = "#,##0.00"

    # ── Ширины колонок ─────────────────────────────────────────────────────────
    from openpyxl.utils import get_column_letter
    widths = {1: 5, 2: 8, 3: 26, 4: 16, 5: 16, 6: 18, 7: 12}
    # Обоснования — текст, им нужна ширина; примечание в хвосте тоже.
    for col in (premium_reason_col, kpi_reason_col, deduction_reason_col, note_col):
        widths[col] = 34
    for col in range(1, n_cols + 1):
        ws.column_dimensions[get_column_letter(col)].width = widths.get(col, 13)
    ws.row_dimensions[header_row].height = 42

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
