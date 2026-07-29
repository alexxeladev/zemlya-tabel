"""
Импорт сотрудников из Excel (task_employee_import).

Три вещи в одном месте:
  1. `generate_import_template` — скачиваемый шаблон .xlsx: строка заголовков,
     строка-пример (серым, помечена «ПРИМЕР — удалите эту строку») и лист
     «Справочники» с допустимыми компаниями/отделами/графиками;
  2. `parse_import_file` — «умный» разбор файла: нормализация значений
     (`5\\2` → `5/2`, `ООО "Комфорт"` → компания справочника, «50 000» → число)
     и валидация каждой строки для превью, БЕЗ записи в БД;
  3. `import_valid_rows` — создание сотрудников по валидным строкам после
     подтверждения (через `build_employee`, как обычный CRUD).

Колонки фиксированные и разбираются ПОЗИЦИОННО (см. `COLUMNS`) — заголовки
нужны человеку, парсер на них не опирается.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.departments import Department
from app.models.schedules import Schedule

# Строка-пример помечается этим текстом в первой колонке; парсер такие строки
# пропускает (пользователь может её и удалить — тогда данные идут со 2-й строки).
EXAMPLE_MARKER = "ПРИМЕР — удалите эту строку"


@dataclass(frozen=True)
class _Column:
    key: str
    title: str
    example: str
    hint: str
    width: int


COLUMNS: tuple[_Column, ...] = (
    _Column("tab_number", "Табельный номер", EXAMPLE_MARKER,
            "необязательно; дубль — ошибка строки", 30),
    _Column("full_name", "ФИО *", "Иванов Иван Иванович",
            "обязательно", 30),
    _Column("company", "Компания (основная) *", 'ООО "Комфорт"',
            "обязательно; должна быть в справочнике (код или название)", 26),
    _Column("department", "Отдел", "ИТО",
            "пусто — без отдела; иначе должен быть в справочнике", 20),
    _Column("position", "Должность", "Инженер",
            "любой текст", 24),
    _Column("schedule", "График", "5/2",
            "должен быть в справочнике: 5/2, 2/2 смена 1 …", 18),
    _Column("pay_type", "Тип оплаты", "окладная",
            "окладная / посменная (по умолчанию окладная)", 16),
    _Column("rate", "Оклад", "50 000",
            "для окладной; число", 14),
    _Column("shift_rate", "Ставка за смену", "",
            "для посменной; число", 16),
    _Column("weekend_pay_type", "Оплата выходных", "коэффициент",
            "коэффициент / фикс (по умолчанию коэффициент)", 18),
    _Column("weekend_value", "Коэффициент / ставка выходных", "1,5",
            "1,5 для коэффициента или 740 для фикс. ставки", 24),
    _Column("hire_date", "Дата приёма", "01.03.2026",
            "ДД.ММ.ГГГГ или дата Excel", 16),
)

_HEADER_FILL = PatternFill("solid", fgColor="FFD9E1F2")
_EXAMPLE_FILL = PatternFill("solid", fgColor="FFF2F2F2")
_HINT_FILL = PatternFill("solid", fgColor="FFFFF9E5")


def generate_import_template(db: Session) -> bytes:
    """Шаблон для заполнения: лист «Сотрудники» + лист «Справочники»."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Сотрудники"

    header_font = Font(name="Arial", size=10, bold=True)
    example_font = Font(name="Arial", size=10, italic=True, color="FF808080")
    hint_font = Font(name="Arial", size=8, italic=True, color="FF9C6500")
    wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for idx, col in enumerate(COLUMNS, start=1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = col.width

        head = ws.cell(row=1, column=idx, value=col.title)
        head.font = header_font
        head.fill = _HEADER_FILL
        head.alignment = wrap

        example = ws.cell(row=2, column=idx, value=col.example or None)
        example.font = example_font
        example.fill = _EXAMPLE_FILL
        example.alignment = Alignment(vertical="center", wrap_text=True)

        hint = ws.cell(row=3, column=idx, value=col.hint)
        hint.font = hint_font
        hint.fill = _HINT_FILL
        hint.alignment = wrap

    ws.row_dimensions[1].height = 32
    ws.row_dimensions[3].height = 30
    ws.freeze_panes = "A4"

    _write_reference_sheet(wb, db)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_reference_sheet(wb: Workbook, db: Session) -> None:
    """Лист со списком допустимых значений справочников — чтобы заполняющий
    не гадал, как называется компания или график (иначе строка будет с ошибкой)."""
    ws = wb.create_sheet("Справочники")
    bold = Font(name="Arial", size=10, bold=True)

    companies = db.query(Company).filter(Company.is_active.is_(True)).order_by(Company.name).all()
    departments = (
        db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    )
    schedules = (
        db.query(Schedule).filter(Schedule.is_active.is_(True)).order_by(Schedule.name).all()
    )

    blocks = [
        ("Компании (код — название)", [f"{c.code} — {c.name}" for c in companies]),
        ("Отделы", [d.name for d in departments]),
        ("Графики", [s.name for s in schedules]),
    ]

    for col_idx, (title, values) in enumerate(blocks, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 36
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.font = bold
        cell.fill = _HEADER_FILL
        for row_idx, value in enumerate(values, start=2):
            ws.cell(row=row_idx, column=col_idx, value=value)

    ws.freeze_panes = "A2"


# ── Нормализация значений ─────────────────────────────────────────────────────

# Пробелы, которые Excel/копипаста приносят вместо обычного: неразрывный, узкий и пр.
_SPACE_CHARS = "\u00a0\u2007\u202f\u2009\u2002\u2003\u2004\u2005\u2006\u2008\u200a\ufeff\t"
_QUOTE_CHARS = '"«»“”„‘’\''
_LEGAL_FORMS = ("ооо", "оао", "зао", "пао", "нао", "ао", "ип")


def cell_text(value: object) -> str:
    """Значение ячейки как текст: спецпробелы → обычные, схлопнуть, обрезать."""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    for ch in _SPACE_CHARS:
        text = text.replace(ch, " ")
    return re.sub(r"\s+", " ", text).strip()


def match_key(value: object) -> str:
    """Ключ для сопоставления со справочником: регистр и «ё» не различаем."""
    return cell_text(value).lower().replace("ё", "е")


def normalize_schedule_key(value: object) -> str:
    """`5\\2`, `5 / 2`, ` 5/2 ` → `5/2` (регистронезависимо)."""
    key = match_key(value).replace("\\", "/")
    return re.sub(r"\s*/\s*", "/", key)


def company_keys(value: object) -> list[str]:
    """Варианты ключа компании: как есть, без кавычек, без правовой формы.

    `ООО "Комфорт"` даёт ключи `ооо "комфорт"`, `ооо комфорт`, `комфорт` —
    поэтому сопоставляется и с полным названием в справочнике, и с коротким.
    """
    base = match_key(value)
    if not base:
        return []
    keys = [base]

    unquoted = base
    for ch in _QUOTE_CHARS:
        unquoted = unquoted.replace(ch, " ")
    unquoted = re.sub(r"\s+", " ", unquoted).strip()
    if unquoted and unquoted not in keys:
        keys.append(unquoted)

    for form in _LEGAL_FORMS:
        if unquoted.startswith(f"{form} "):
            short = unquoted[len(form) + 1:].strip()
            if short and short not in keys:
                keys.append(short)
            break

    return keys


def parse_decimal(value: object) -> Decimal | None:
    """«50 000», «50 000,50», 50000.0 → Decimal. Пусто → None, мусор → ValueError."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("не число")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))

    text = cell_text(value)
    if not text:
        return None
    text = text.replace(" ", "")
    text = re.sub(r"(?i)(руб\.?|р\.|₽)$", "", text)
    text = text.replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("не число") from exc


_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y.%m.%d")
# Excel хранит даты числом дней от 30.12.1899 (учитывая его «1900 високосный»).
_EXCEL_EPOCH = datetime.date(1899, 12, 30)


def parse_date(value: object) -> datetime.date | None:
    """Дата из ячейки: объект даты, число Excel или строка в частых форматах."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, bool):
        raise ValueError("не дата")
    if isinstance(value, (int, float)):
        serial = int(value)
        if serial <= 0:
            raise ValueError("не дата")
        return _EXCEL_EPOCH + datetime.timedelta(days=serial)

    text = cell_text(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("не дата")


def parse_pay_type(value: object) -> str | None:
    """«окладная»/«оклад»/«salary» → salary; «посменная»/«за смену» → per_shift.

    Пусто → salary (дефолт), непонятное → None (ошибка строки).
    """
    key = match_key(value)
    if not key:
        return "salary"
    if "смен" in key or "shift" in key:
        return "per_shift"
    if "оклад" in key or "salary" in key:
        return "salary"
    return None


def parse_weekend_pay_type(value: object) -> str | None:
    """«коэффициент»/«коэф»/«×1.5» → coefficient; «фикс»/«740» → fixed_rate.

    Пусто → coefficient (дефолт), непонятное → None (ошибка строки).
    """
    key = match_key(value)
    if not key:
        return "coefficient"
    if "фикс" in key or "fix" in key:
        return "fixed_rate"
    if "коэф" in key or "coef" in key or key.startswith(("×", "x", "*")):
        return "coefficient"
    # Голое число: 1,5 — это коэффициент, 740 — ставка за час.
    try:
        number = parse_decimal(key)
    except ValueError:
        return None
    if number is None:
        return "coefficient"
    return "fixed_rate" if number >= 10 else "coefficient"
