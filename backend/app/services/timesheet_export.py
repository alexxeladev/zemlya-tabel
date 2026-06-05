from __future__ import annotations

import calendar as _cal
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.services.calendar import get_month_data, parse_days_string
from app.services.timesheet import get_month_entries, visible_employees_for_actor

_ORG_NAME = "ДЕВЕЛОПМЕНТ ГРУППА «ЗЕМЛЯ МО»"

# ── Style helpers ─────────────────────────────────────────────────────────────

def _thin_side() -> Side:
    return Side(style="thin", color="000000")


def _thin_border() -> Border:
    s = _thin_side()
    return Border(left=s, right=s, top=s, bottom=s)


def _holiday_fill() -> PatternFill:
    return PatternFill("solid", fgColor="FFFFCCCC")  # ARGB: red tint


def _short_fill() -> PatternFill:
    return PatternFill("solid", fgColor="FFFFF2CC")  # ARGB: yellow tint


def _weekend_fill() -> PatternFill:
    return PatternFill("solid", fgColor="FFEFEFEF")  # ARGB: gray tint


def _header_font(bold: bool = True) -> Font:
    return Font(name="Arial", size=9, bold=bold)


def _apply_border(ws, row: int, col: int) -> None:
    ws.cell(row=row, column=col).border = _thin_border()


def _set_cell(ws, row: int, col: int, value=None, *, bold=False, center=False,
              wrap=False, fill=None, font_color="000000") -> None:
    c = ws.cell(row=row, column=col)
    if value is not None:
        c.value = value
    c.font = Font(name="Arial", size=9, bold=bold, color=font_color)
    alignment_kwargs: dict = {"wrap_text": wrap}
    if center:
        alignment_kwargs["horizontal"] = "center"
        alignment_kwargs["vertical"] = "center"
    c.alignment = Alignment(**alignment_kwargs)
    c.border = _thin_border()
    if fill is not None:
        c.fill = fill


# ── Column layout ─────────────────────────────────────────────────────────────

# Fixed left columns (1-based): №, ФИО, Должность, Таб.№, Компания
_COL_NUM = 1
_COL_NAME = 2
_COL_POS = 3
_COL_TAB = 4
_COL_COMPANY = 5
_FIXED_COLS = 5


def _day_col(day: int, total_days: int) -> int:
    """Column index for a calendar day (1-based day → column index)."""
    if day <= 15:
        return _FIXED_COLS + day            # days 1-15 → cols 6-20
    # After day 15 there's the subtotal1 column at col 21, so day 16 starts at 22
    return _FIXED_COLS + 15 + 1 + (day - 15)  # day 16 → 22, day 30 → 36


def _subtotal1_col() -> int:
    """Column for 'Итого 1-15'."""
    return _FIXED_COLS + 15 + 1


def _subtotal2_col(total_days: int) -> int:
    """Column for 'Итого 16-end'."""
    days_second_half = total_days - 15
    return _FIXED_COLS + 15 + 1 + days_second_half + 1


def _total_col(total_days: int) -> int:
    """Column for 'Итого за месяц'."""
    return _subtotal2_col(total_days) + 1


def _total_columns(total_days: int) -> int:
    return _total_col(total_days)


# ── Main generator ────────────────────────────────────────────────────────────

def generate_t13_excel(
    db: Session,
    actor: Employee,
    year: int,
    month: int,
    department_id: int | None = None,
) -> bytes:
    """
    Генерирует Excel-файл с табелем Т-13 за указанный период.
    Возвращает bytes — содержимое .xlsx файла.
    """
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)

    # Производственный календарь (опционально — не падаем если нет)
    cal_record = db.query(ProductionCalendar).filter_by(year=year).first()
    calendar_data: dict | None = cal_record.data if cal_record else None

    # Компании
    companies_by_id: dict[int, Company] = {
        c.id: c for c in db.query(Company).filter(Company.is_active == True).all()  # noqa: E712
    }

    # Индекс entries: {(employee_id, day): {company_id: hours}}
    entries_index: dict[tuple[int, int], dict[int, float]] = {}
    for e in entries:
        day = e.work_date.day
        key = (e.employee_id, day)
        if key not in entries_index:
            entries_index[key] = {}
        entries_index[key][e.company_id] = float(e.hours)

    # Для каждого сотрудника — список компаний, по которым есть entries в этом месяце
    emp_companies: dict[int, list[int]] = {}
    for emp in employees:
        used: set[int] = set()
        for e in entries:
            if e.employee_id == emp.id:
                used.add(e.company_id)
        if not used:
            continue  # сотрудник без entries — пропускаем
        # Сортируем по id для детерминизма
        emp_companies[emp.id] = sorted(used)

    total_days = _cal.monthrange(year, month)[1]

    # Определяем типы дней
    non_working: set[int] = set()
    short_days: set[int] = set()
    if calendar_data:
        month_data = get_month_data(calendar_data, month)
        if month_data:
            non_working, short_days = parse_days_string(month_data["days"])
    # Выходные по ISO-weekday (6=Сб, 7=Вс → weekday() 5,6)
    weekends: set[int] = {
        d for d in range(1, total_days + 1)
        if date(year, month, d).weekday() >= 5
    }

    wb = Workbook()
    ws = wb.active
    ws.title = f"Т-13 {year}-{month:02d}"

    # ── Ширины колонок ───────────────────────────────────────────────────────
    ws.column_dimensions[get_column_letter(_COL_NUM)].width = 5
    ws.column_dimensions[get_column_letter(_COL_NAME)].width = 28
    ws.column_dimensions[get_column_letter(_COL_POS)].width = 15
    ws.column_dimensions[get_column_letter(_COL_TAB)].width = 8
    ws.column_dimensions[get_column_letter(_COL_COMPANY)].width = 16
    for d in range(1, total_days + 1):
        ws.column_dimensions[get_column_letter(_day_col(d, total_days))].width = 4
    ws.column_dimensions[get_column_letter(_subtotal1_col())].width = 8
    ws.column_dimensions[get_column_letter(_subtotal2_col(total_days))].width = 8
    ws.column_dimensions[get_column_letter(_total_col(total_days))].width = 8

    # ── Шапка документа ─────────────────────────────────────────────────────
    cur_row = _write_document_header(ws, year, month, department_id, db, total_days)

    # ── Шапка таблицы ───────────────────────────────────────────────────────
    cur_row = _write_table_header(ws, cur_row, year, month, total_days,
                                  non_working, short_days, weekends)

    # ── Строки сотрудников ──────────────────────────────────────────────────
    seq = 0
    for emp in employees:
        if emp.id not in emp_companies:
            continue
        company_ids = emp_companies[emp.id]
        seq += 1
        cur_row = _write_employee_rows(
            ws, cur_row, seq, emp, company_ids, companies_by_id,
            entries_index, total_days,
        )

    # ── Подвал с подписями ──────────────────────────────────────────────────
    _write_footer(ws, cur_row + 2)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Document header ───────────────────────────────────────────────────────────

def _write_document_header(
    ws, year: int, month: int, department_id: int | None, db: Session, total_days: int
) -> int:
    """Пишет шапку документа, возвращает следующую строку."""

    # Определяем подразделение
    dept_name = "Все отделы"
    if department_id is not None:
        from app.models.departments import Department
        dept = db.get(Department, department_id)
        if dept:
            dept_name = dept.name

    last_day = _cal.monthrange(year, month)[1]
    period_start = f"01.{month:02d}.{year}"
    period_end = f"{last_day:02d}.{month:02d}.{year}"

    right_col = _total_col(total_days)

    # Строка 1: «Унифицированная форма...»
    ws.merge_cells(start_row=1, start_column=right_col - 3,
                   end_row=1, end_column=right_col)
    ws.cell(row=1, column=right_col - 3).value = "Унифицированная форма № Т-13"
    ws.cell(row=1, column=right_col - 3).font = Font(name="Arial", size=8)

    # Строка 2: название организации
    ws.cell(row=2, column=1).value = _ORG_NAME
    ws.cell(row=2, column=1).font = Font(name="Arial", size=10, bold=True)

    # Строка 3: «Форма по ОКУД»
    ws.merge_cells(start_row=3, start_column=right_col - 3,
                   end_row=3, end_column=right_col)
    ws.cell(row=3, column=right_col - 3).value = "Форма по ОКУД: 0301008"
    ws.cell(row=3, column=right_col - 3).font = Font(name="Arial", size=8)

    # Строка 4: подразделение
    ws.cell(row=4, column=1).value = f"Структурное подразделение: {dept_name}"
    ws.cell(row=4, column=1).font = Font(name="Arial", size=9)

    # Строка 5: ТАБЕЛЬ
    ws.merge_cells(start_row=6, start_column=1, end_row=6, end_column=right_col)
    c = ws.cell(row=6, column=1)
    c.value = "ТАБЕЛЬ УЧЁТА РАБОЧЕГО ВРЕМЕНИ"
    c.font = Font(name="Arial", size=14, bold=True)
    c.alignment = Alignment(horizontal="center")

    # Строка 7: период
    ws.merge_cells(start_row=7, start_column=1, end_row=7, end_column=right_col)
    c = ws.cell(row=7, column=1)
    c.value = f"Отчётный период: с {period_start} по {period_end}"
    c.font = Font(name="Arial", size=10)
    c.alignment = Alignment(horizontal="center")

    ws.row_dimensions[8].height = 6  # пустая строка-разделитель

    return 9  # первая строка таблицы


# ── Table header ──────────────────────────────────────────────────────────────

def _write_table_header(
    ws, start_row: int, year: int, month: int, total_days: int,
    non_working: set[int], short_days: set[int], weekends: set[int],
) -> int:
    r = start_row
    weekday_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    # Заголовки фиксированных колонок — объединяем 2 строки
    fixed_headers = [
        (_COL_NUM, "№"),
        (_COL_NAME, "ФИО"),
        (_COL_POS, "Должность"),
        (_COL_TAB, "Таб. №"),
        (_COL_COMPANY, "Компания"),
    ]
    for col, label in fixed_headers:
        ws.merge_cells(start_row=r, start_column=col, end_row=r + 1, end_column=col)
        c = ws.cell(row=r, column=col)
        c.value = label
        c.font = _header_font()
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = _thin_border()
        ws.cell(row=r + 1, column=col).border = _thin_border()

    # День 1-15: строка 1 — «1-я половина месяца», строка 2 — числа
    first_half_start = _day_col(1, total_days)
    first_half_end = _day_col(15, total_days)
    ws.merge_cells(start_row=r, start_column=first_half_start,
                   end_row=r, end_column=first_half_end)
    c = ws.cell(row=r, column=first_half_start)
    c.value = "1-я половина"
    c.font = _header_font()
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border = _thin_border()

    # Subtotal 1-15
    st1 = _subtotal1_col()
    ws.merge_cells(start_row=r, start_column=st1, end_row=r + 1, end_column=st1)
    c = ws.cell(row=r, column=st1)
    c.value = "Итого\n1-15"
    c.font = _header_font()
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = _thin_border()
    ws.cell(row=r + 1, column=st1).border = _thin_border()

    # День 16-end: строка 1 — «2-я половина»
    second_half_start = _day_col(16, total_days)
    second_half_end = _day_col(total_days, total_days)
    ws.merge_cells(start_row=r, start_column=second_half_start,
                   end_row=r, end_column=second_half_end)
    c = ws.cell(row=r, column=second_half_start)
    c.value = "2-я половина"
    c.font = _header_font()
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border = _thin_border()

    # Subtotal 16-end
    st2 = _subtotal2_col(total_days)
    ws.merge_cells(start_row=r, start_column=st2, end_row=r + 1, end_column=st2)
    c = ws.cell(row=r, column=st2)
    c.value = f"Итого\n16-{total_days}"
    c.font = _header_font()
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = _thin_border()
    ws.cell(row=r + 1, column=st2).border = _thin_border()

    # Итого за месяц
    tc = _total_col(total_days)
    ws.merge_cells(start_row=r, start_column=tc, end_row=r + 1, end_column=tc)
    c = ws.cell(row=r, column=tc)
    c.value = "Итого\nза месяц"
    c.font = _header_font()
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = _thin_border()
    ws.cell(row=r + 1, column=tc).border = _thin_border()

    # Вторая строка заголовка: числа дней
    for d in range(1, total_days + 1):
        col = _day_col(d, total_days)
        fill = None
        font_color = "000000"
        if d in non_working:
            fill = _holiday_fill()
            font_color = "CC0000"
        elif d in short_days:
            fill = _short_fill()
            font_color = "7D6608"
        elif d in weekends:
            fill = _weekend_fill()
            font_color = "666666"

        wd = date(year, month, d).weekday()
        label = f"{d}\n{weekday_ru[wd]}"
        c = ws.cell(row=r + 1, column=col)
        c.value = label
        c.font = Font(name="Arial", size=8, bold=True, color=font_color)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = _thin_border()
        if fill:
            c.fill = fill

    ws.row_dimensions[r].height = 20
    ws.row_dimensions[r + 1].height = 26
    return r + 2


# ── Employee rows ─────────────────────────────────────────────────────────────

def _write_employee_rows(
    ws,
    start_row: int,
    seq: int,
    emp: Employee,
    company_ids: list[int],
    companies_by_id: dict[int, Company],
    entries_index: dict[tuple[int, int], dict[int, float]],
    total_days: int,
) -> int:
    n = len(company_ids)
    end_row = start_row + n - 1

    # № п/п — merge по всем строкам сотрудника
    if n > 1:
        ws.merge_cells(start_row=start_row, start_column=_COL_NUM,
                       end_row=end_row, end_column=_COL_NUM)
    c = ws.cell(row=start_row, column=_COL_NUM)
    c.value = seq
    c.font = _header_font(bold=False)
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border = _thin_border()
    for r in range(start_row + 1, end_row + 1):
        ws.cell(row=r, column=_COL_NUM).border = _thin_border()

    # ФИО — merge
    if n > 1:
        ws.merge_cells(start_row=start_row, start_column=_COL_NAME,
                       end_row=end_row, end_column=_COL_NAME)
    c = ws.cell(row=start_row, column=_COL_NAME)
    c.value = emp.full_name
    c.font = _header_font(bold=False)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    c.border = _thin_border()
    for r in range(start_row + 1, end_row + 1):
        ws.cell(row=r, column=_COL_NAME).border = _thin_border()

    # Должность — merge
    if n > 1:
        ws.merge_cells(start_row=start_row, start_column=_COL_POS,
                       end_row=end_row, end_column=_COL_POS)
    c = ws.cell(row=start_row, column=_COL_POS)
    c.value = emp.position or ""
    c.font = _header_font(bold=False)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    c.border = _thin_border()
    for r in range(start_row + 1, end_row + 1):
        ws.cell(row=r, column=_COL_POS).border = _thin_border()

    # Таб. № — merge
    if n > 1:
        ws.merge_cells(start_row=start_row, start_column=_COL_TAB,
                       end_row=end_row, end_column=_COL_TAB)
    c = ws.cell(row=start_row, column=_COL_TAB)
    c.value = emp.tab_number or ""
    c.font = _header_font(bold=False)
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border = _thin_border()
    for r in range(start_row + 1, end_row + 1):
        ws.cell(row=r, column=_COL_TAB).border = _thin_border()

    # Строки по компаниям
    for i, comp_id in enumerate(company_ids):
        row = start_row + i
        comp = companies_by_id.get(comp_id)
        comp_name = comp.name if comp else f"Компания #{comp_id}"

        # Название компании
        c = ws.cell(row=row, column=_COL_COMPANY)
        c.value = comp_name
        c.font = _header_font(bold=False)
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        c.border = _thin_border()

        total_hours = 0.0
        first_half = 0.0
        second_half = 0.0

        for d in range(1, total_days + 1):
            col = _day_col(d, total_days)
            hours = entries_index.get((emp.id, d), {}).get(comp_id)
            c = ws.cell(row=row, column=col)
            c.border = _thin_border()
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.font = Font(name="Arial", size=9)
            if hours is not None and hours > 0:
                c.value = int(hours) if hours == int(hours) else hours
                total_hours += hours
                if d <= 15:
                    first_half += hours
                else:
                    second_half += hours

        # Итого 1-15
        c = ws.cell(row=row, column=_subtotal1_col())
        c.value = int(first_half) if first_half == int(first_half) else first_half
        c.font = _header_font()
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = _thin_border()

        # Итого 16-end
        c = ws.cell(row=row, column=_subtotal2_col(total_days))
        c.value = int(second_half) if second_half == int(second_half) else second_half
        c.font = _header_font()
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = _thin_border()

        # Итого за месяц
        c = ws.cell(row=row, column=_total_col(total_days))
        c.value = int(total_hours) if total_hours == int(total_hours) else total_hours
        c.font = _header_font(bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = _thin_border()

    return end_row + 1


# ── Footer ────────────────────────────────────────────────────────────────────

def _write_footer(ws, start_row: int) -> None:
    lines = [
        ("Ответственное лицо:", "должность", "подпись", "расшифровка подписи"),
        ("Руководитель:", "должность", "подпись", "расшифровка подписи"),
        ("Работник кадровой службы:", "должность", "подпись", "расшифровка подписи"),
    ]
    r = start_row
    for title, *labels in lines:
        ws.cell(row=r, column=1).value = title
        ws.cell(row=r, column=1).font = Font(name="Arial", size=9)
        # Три линии для подписей
        for k, lbl in enumerate(labels):
            col = 3 + k * 3
            ws.cell(row=r, column=col).value = "______________________________"
            ws.cell(row=r, column=col).font = Font(name="Arial", size=9)
            ws.cell(row=r + 1, column=col).value = lbl
            ws.cell(row=r + 1, column=col).font = Font(name="Arial", size=8, color="666666")
            ws.cell(row=r + 1, column=col).alignment = Alignment(horizontal="center")
        r += 3  # строка с линиями + подписи + пустая между блоками
