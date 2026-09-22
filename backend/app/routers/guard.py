"""
API модуля «Вахта» (task_vahta): настройки, табель, мутации, выгрузка.

Права (см. `app.services.org_access`, второго списка ролей не заводим):

* **настройки** (экипажи, посты) — admin и менеджер отдела охраны;
* **табель** — они же, плюс бухгалтер на просмотр и табельщик отдела охраны;
* **табельщик денег не получает вовсе** — суммы вычищаются в
  `services/guard_month.py` на уровне ответа, а финансовые эндпойнты (правка
  премии/штрафа/оф. выплаты и ставки) ему закрыты 403;
* **employee** раздела не видит.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user
from app.database import get_db
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.guard_assignments import GuardAssignment
from app.models.guard_job_titles import GuardJobTitle
from app.models.guard_posts import (
    GuardCrew,
    GuardPost,
    GuardSite,
    GuardZone,
)
from app.models.positions import EmployeePosition
from app.schemas.guard import (
    GuardAssignmentCreate,
    GuardAssignmentUpdate,
    GuardCandidateRead,
    GuardCrewCreate,
    GuardCrewRead,
    GuardCrewUpdate,
    GuardDayInput,
    GuardDaysInput,
    GuardMonthRead,
    GuardPostCreate,
    GuardPostRead,
    GuardPostUpdate,
    GuardQuickHireInput,
    GuardReplaceInput,
    GuardSettingsRead,
    GuardSettingsUpdate,
    GuardJobTitleCreate,
    GuardJobTitleRead,
    GuardJobTitleUpdate,
    GuardShareRead,
    GuardStaffCreate,
    GuardStaffRead,
    GuardStaffUpdate,
    GuardSimilarEmployee,
    GuardSiteCreate,
    GuardSiteRead,
    GuardSiteUpdate,
    GuardZoneCreate,
    GuardZoneRead,
    GuardZoneUpdate,
)
from app.services.company_order import company_display_name
from app.services.company_shares import SharesValidationError, validate_shares
from app.services.guard_duty import (
    GuardError,
    add_position_for_guard,
    copy_previous_period,
    create_assignment,
    create_crew,
    create_post,
    create_site,
    create_zone,
    delete_assignment,
    delete_crew,
    delete_post,
    delete_site,
    delete_zone,
    employer_tax_percent,
    find_similar_employees,
    guard_department_ids,
    list_assignments,
    list_crews,
    list_posts,
    list_sites,
    list_zones,
    quick_hire,
    replace_on_post,
    require_department_access,
    set_crew_shares,
    set_days,
    set_employer_tax_percent,
    set_site_shares,
    toggle_day,
    update_crew,
    update_post,
    update_site,
    update_zone,
)
from app.services.guard_export import generate_guard_timesheet_excel
from app.services.guard_job_titles import (
    GuardJobTitleClosedMonths,
    delete_job_title,
    get_job_title,
    job_title_of_position,
    title_index,
    usage_counts,
    list_job_titles,
    save_job_title,
)
from app.services.guard_staff import (
    OfficialRemovalWarning,
    amount_of,
    create_staff,
    is_guard_position,
    list_staff_positions,
    update_staff,
)
from app.services.guard_month import build_guard_month, can_edit_vahta
from app.services.org_access import can_see_finances
from app.services.timesheet_periods import month_lock_status

router = APIRouter()

#: Роли, которым раздел доступен вообще. employee сюда не входит.
_VAHTA_ROLES = ("admin", "accountant", "manager", "timekeeper")
#: Кто правит справочник вахты. Бухгалтер смотрит, но настройки не меняет.
_SETTINGS_ROLES = ("admin", "manager")


def _require_vahta(actor: Employee) -> None:
    if actor.role not in _VAHTA_ROLES:
        raise HTTPException(status_code=403, detail="Раздел «Вахта» недоступен")


def _require_settings(actor: Employee) -> None:
    if actor.role not in _SETTINGS_ROLES:
        raise HTTPException(
            status_code=403, detail="Настройки вахты доступны администратору и менеджеру охраны"
        )


def _require_timesheet_edit(actor: Employee) -> None:
    """Правка табеля вахты (task_stage2_access п.2.7). Было: мутации проверяли
    только доступ к разделу, и бухгалтер — «только просмотр» на экране — через
    API ставил людей, отмечал смены, менял премии и удалял строки."""
    _require_vahta(actor)
    if not can_edit_vahta(actor):
        raise HTTPException(status_code=403, detail="Табель вахты для этой роли — только просмотр")


def _require_money(actor: Employee) -> None:
    """Денежные поля вахты. Табельщик ведёт смены, но премий и ставок не трогает."""
    if not can_see_finances(actor):
        raise HTTPException(status_code=403, detail="Нет доступа к финансовым данным")


def _guard_error(exc: GuardError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


_PERIOD_LOCK_REASON = {
    "pending_review": "на проверке у бухгалтера",
    "closed": "закрыт",
}


def _require_open_month(
    db: Session, department_id: int | None, year: int, month: int
) -> None:
    """Назначения вахты правятся только в периоде-ЧЕРНОВИКЕ (task_stage1 п.1.4).

    Своих статусов у вахты нет: берётся период табеля охранного подразделения за
    этот месяц, правило то же, что у ячеек табеля, — `pending_review` блокирует
    наравне с `closed`: месяц, отправленный бухгалтеру, не должен меняться под
    проверяющим. Снапшота расчёта в системе нет, поэтому любая правка назначения
    — человек, дни, ставка, премия — пересчитала бы ведомость, которую уже
    смотрят. Нужно поправить — период возвращают в черновик.
    """
    # for_write: строка периода под блокировкой до коммита правки (п.1.5).
    lock = month_lock_status(db, department_id, year, month, for_write=True)
    if lock is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Период {month:02d}.{year} {_PERIOD_LOCK_REASON.get(lock, lock)} — "
                "назначения вахты в нём не меняются. Верните период в черновик, "
                "чтобы внести правку"
            ),
        )


def _access(db: Session, actor: Employee, department_id: int) -> None:
    try:
        require_department_access(db, actor, department_id)
    except GuardError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


# ── Настройки вахты ───────────────────────────────────────────────────────────

@router.get("/settings", response_model=GuardSettingsRead)
def get_settings(
    db: Session = Depends(get_db), actor: Employee = Depends(get_current_user)
):
    """Ставка налога на официальную часть выплаты — деньги, табельщику 403."""
    _require_vahta(actor)
    _require_money(actor)
    return GuardSettingsRead(employer_tax_percent=employer_tax_percent(db))


@router.patch("/settings", response_model=GuardSettingsRead)
def patch_settings(
    payload: GuardSettingsUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Сменить ставку налога (admin и менеджер охраны, как прочие настройки).

    Ставка одна на все месяцы: правка пересчитывает разнесение и прошлых.
    """
    _require_settings(actor)
    before = employer_tax_percent(db)
    try:
        settings = set_employer_tax_percent(db, payload.employer_tax_percent)
    except GuardError as exc:
        raise _guard_error(exc)
    if before != payload.employer_tax_percent:
        log_action(
            db, actor, "guard_settings", settings.id, "update",
            before={"employer_tax_percent": str(before)},
            after={"employer_tax_percent": str(payload.employer_tax_percent)},
        )
    db.commit()
    return GuardSettingsRead(employer_tax_percent=employer_tax_percent(db))


# ── Отделы охраны ─────────────────────────────────────────────────────────────

# ── Справочник должностей охраны ──────────────────────────────────────────────
#
# Должность — строка справочника, а не константа кода. Читают все роли вахты
# (табельщик выбирает должность, ставя человека на пост), правят — те же, кто
# правит настройки: admin и менеджер охраны.

def _job_title_read(title, usage: dict[int, tuple[int, int, int]]) -> GuardJobTitleRead:
    rows, closed, staff = usage.get(title.id, (0, 0, 0))
    return GuardJobTitleRead(
        id=title.id, name=title.name, pay_type=title.pay_type,
        pay_type_label=title.pay_type_label,
        default_for_post=title.default_for_post, default_for_crew=title.default_for_crew,
        sort_order=title.sort_order, is_active=title.is_active,
        usage_count=rows, closed_usage_count=closed, staff_count=staff,
    )


@router.get("/job-titles", response_model=list[GuardJobTitleRead])
def get_job_titles(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_vahta(actor)
    usage = usage_counts(db)
    return [_job_title_read(t, usage) for t in list_job_titles(db, include_inactive=include_inactive)]


@router.post("/job-titles", response_model=GuardJobTitleRead, status_code=status.HTTP_201_CREATED)
def post_job_title(
    payload: GuardJobTitleCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_vahta(actor)
    _require_settings(actor)
    try:
        title = save_job_title(db, None, payload.model_dump(exclude_unset=True))
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(db, actor, "guard_job_title", title.id, "create",
               after={"name": title.name, "pay_type": title.pay_type})
    db.commit()
    db.refresh(title)
    return _job_title_read(title, usage_counts(db))


@router.patch("/job-titles/{title_id}", response_model=GuardJobTitleRead)
def patch_job_title(
    title_id: int,
    payload: GuardJobTitleUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Смена способа оплаты пересчитывает все НЕЗАКРЫТЫЕ месяцы, где стоят люди
    с этой должностью, — экран об этом предупреждает (`usage_count`)."""
    _require_vahta(actor)
    _require_settings(actor)
    title = db.get(GuardJobTitle, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Должность не найдена")
    before = {"name": title.name, "pay_type": title.pay_type, "is_active": title.is_active}
    try:
        save_job_title(db, title, payload.model_dump(exclude_unset=True))
    except GuardJobTitleClosedMonths as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(db, actor, "guard_job_title", title.id, "update", before=before,
               after={"name": title.name, "pay_type": title.pay_type, "is_active": title.is_active})
    db.commit()
    db.refresh(title)
    return _job_title_read(title, usage_counts(db))


@router.delete("/job-titles/{title_id}")
def remove_job_title(
    title_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_vahta(actor)
    _require_settings(actor)
    title = db.get(GuardJobTitle, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Должность не найдена")
    try:
        result = delete_job_title(db, title)
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(db, actor, "guard_job_title", title_id, "delete", before={"name": title.name})
    db.commit()
    return {"result": result}


@router.get("/departments")
def get_guard_departments(
    db: Session = Depends(get_db), actor: Employee = Depends(get_current_user)
):
    """Подразделения охраны, доступные текущему пользователю."""
    _require_vahta(actor)
    ids = guard_department_ids(db, actor)
    rows = db.query(Department).filter(Department.id.in_(ids or [0])).all()
    return [{"id": d.id, "name": d.name, "code": d.code} for d in rows]


# ── Справочник: зоны обслуживания ─────────────────────────────────────────────

def _zone_read(zone: GuardZone) -> GuardZoneRead:
    return GuardZoneRead(
        id=zone.id,
        name=zone.name,
        department_id=zone.department_id,
        sort_order=zone.sort_order,
        is_active=zone.is_active,
        site_count=sum(1 for s in zone.sites if s.is_active),
        crew_count=sum(1 for c in zone.crews if c.is_active),
    )


@router.get("/zones", response_model=list[GuardZoneRead])
def get_zones(
    db: Session = Depends(get_db), actor: Employee = Depends(get_current_user)
):
    """Зоны обслуживания — верхний уровень справочника и группировки табеля."""
    _require_vahta(actor)
    return [_zone_read(z) for z in list_zones(db, guard_department_ids(db, actor))]


@router.post("/zones", response_model=GuardZoneRead, status_code=status.HTTP_201_CREATED)
def post_zone(
    payload: GuardZoneCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    _access(db, actor, payload.department_id)
    try:
        zone = create_zone(db, payload.model_dump())
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(db, actor, "guard_zone", zone.id, "create", after={"name": zone.name})
    db.commit()
    db.refresh(zone)
    return _zone_read(zone)


@router.patch("/zones/{zone_id}", response_model=GuardZoneRead)
def patch_zone(
    zone_id: int,
    payload: GuardZoneUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    zone = db.get(GuardZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    _access(db, actor, zone.department_id)
    update_zone(db, zone, payload.model_dump(exclude_unset=True))
    log_action(db, actor, "guard_zone", zone.id, "update", after={"name": zone.name})
    db.commit()
    db.refresh(zone)
    return _zone_read(zone)


@router.delete("/zones/{zone_id}")
def remove_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    zone = db.get(GuardZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    _access(db, actor, zone.department_id)
    result = delete_zone(db, zone)
    log_action(db, actor, "guard_zone", zone_id, "delete")
    db.commit()
    return {"result": result}


# ── Справочник: экипажи ГБР ───────────────────────────────────────────────────

def _crew_read(crew: GuardCrew, names: dict[int, tuple[str, str]]) -> GuardCrewRead:
    return GuardCrewRead(
        id=crew.id,
        name=crew.name,
        zone_id=crew.zone_id,
        zone_name=crew.zone.name if crew.zone else None,
        department_id=crew.department_id or 0,
        shift_rate=Decimal(str(crew.shift_rate)),
        sort_order=crew.sort_order,
        is_active=crew.is_active,
        shares=_shares_read(crew.shares, names),
    )


def _shares_read(shares, names: dict[int, tuple[str, str]]) -> list[GuardShareRead]:
    return [
        GuardShareRead(
            company_id=s.company_id,
            percent=Decimal(str(s.percent)),
            company_name=names.get(s.company_id, (None, None))[0],
            company_display_name=names.get(s.company_id, (None, None))[1],
        )
        for s in shares
    ]


def _company_names(db: Session) -> dict[int, tuple[str, str]]:
    return {c.id: (c.name, company_display_name(c)) for c in db.query(Company).all()}


@router.get("/crews", response_model=list[GuardCrewRead])
def get_crews(
    db: Session = Depends(get_db), actor: Employee = Depends(get_current_user)
):
    _require_vahta(actor)
    names = _company_names(db)
    crews = [_crew_read(c, names) for c in list_crews(db, guard_department_ids(db, actor))]
    # Ставка и распределение — деньги: табельщику отдаём только названия.
    if not can_see_finances(actor):
        return [c.model_copy(update={"shift_rate": None, "shares": []}) for c in crews]
    return crews


@router.post("/crews", response_model=GuardCrewRead, status_code=status.HTTP_201_CREATED)
def post_crew(
    payload: GuardCrewCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    zone = db.get(GuardZone, payload.zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    _access(db, actor, zone.department_id)
    try:
        crew = create_crew(db, payload.model_dump(exclude={"shares"}))
        if payload.shares is not None:
            set_crew_shares(db, crew, validate_shares(db, payload.shares))
    except GuardError as exc:
        raise _guard_error(exc)
    except SharesValidationError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc))
    log_action(db, actor, "guard_crew", crew.id, "create", after={"name": crew.name})
    db.commit()
    db.refresh(crew)
    return _crew_read(crew, _company_names(db))


@router.patch("/crews/{crew_id}", response_model=GuardCrewRead)
def patch_crew(
    crew_id: int,
    payload: GuardCrewUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    crew = db.get(GuardCrew, crew_id)
    if crew is None:
        raise HTTPException(status_code=404, detail="Экипаж не найден")
    _access(db, actor, crew.department_id or 0)
    try:
        update_crew(db, crew, payload.model_dump(exclude_unset=True, exclude={"shares"}))
        if payload.shares is not None:
            set_crew_shares(db, crew, validate_shares(db, payload.shares))
    except GuardError as exc:
        raise _guard_error(exc)
    except SharesValidationError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc))
    log_action(db, actor, "guard_crew", crew.id, "update", after={"name": crew.name})
    db.commit()
    db.refresh(crew)
    return _crew_read(crew, _company_names(db))


@router.delete("/crews/{crew_id}")
def remove_crew(
    crew_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    crew = db.get(GuardCrew, crew_id)
    if crew is None:
        raise HTTPException(status_code=404, detail="Экипаж не найден")
    _access(db, actor, crew.department_id or 0)
    result = delete_crew(db, crew)
    log_action(db, actor, "guard_crew", crew.id, "delete")
    db.commit()
    return {"result": result}


# ── Справочник: объекты ───────────────────────────────────────────────────────

def _post_read(post: GuardPost) -> GuardPostRead:
    return GuardPostRead(
        id=post.id,
        site_id=post.site_id,
        site_name=post.site.name if post.site else None,
        name=post.name,
        department_id=post.department_id or 0,
        shift_rate=Decimal(str(post.shift_rate)) if post.shift_rate is not None else None,
        effective_rate=post.effective_rate,
        sort_order=post.sort_order,
        is_active=post.is_active,
    )


def _site_read(site: GuardSite, names: dict[int, tuple[str, str]]) -> GuardSiteRead:
    return GuardSiteRead(
        id=site.id,
        name=site.name,
        zone_id=site.zone_id,
        zone_name=site.zone.name if site.zone else None,
        department_id=site.department_id or 0,
        shift_rate=Decimal(str(site.shift_rate)),
        sort_order=site.sort_order,
        is_active=site.is_active,
        shares=_shares_read(site.shares, names),
        posts=[_post_read(p) for p in site.posts if p.is_active],
    )


def _mask_site(site: GuardSiteRead) -> GuardSiteRead:
    """Табельщику — объект без денег: ставки и проценты вычищаются."""
    return site.model_copy(update={
        "shift_rate": None,
        "shares": [],
        "posts": [
            p.model_copy(update={"shift_rate": None, "effective_rate": None})
            for p in site.posts
        ],
    })


@router.get("/sites", response_model=list[GuardSiteRead])
def get_sites(
    db: Session = Depends(get_db), actor: Employee = Depends(get_current_user)
):
    _require_vahta(actor)
    names = _company_names(db)
    sites = [_site_read(s, names) for s in list_sites(db, guard_department_ids(db, actor))]
    if not can_see_finances(actor):
        return [_mask_site(s) for s in sites]
    return sites


@router.post("/sites", response_model=GuardSiteRead, status_code=status.HTTP_201_CREATED)
def post_site(
    payload: GuardSiteCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    zone = db.get(GuardZone, payload.zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    _access(db, actor, zone.department_id)
    try:
        site = create_site(db, payload.model_dump(exclude={"shares"}))
        if payload.shares is not None:
            set_site_shares(db, site, validate_shares(db, payload.shares))
    except GuardError as exc:
        raise _guard_error(exc)
    except SharesValidationError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc))
    log_action(db, actor, "guard_site", site.id, "create", after={"name": site.name})
    db.commit()
    db.refresh(site)
    return _site_read(site, _company_names(db))


@router.patch("/sites/{site_id}", response_model=GuardSiteRead)
def patch_site(
    site_id: int,
    payload: GuardSiteUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    site = db.get(GuardSite, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Объект не найден")
    _access(db, actor, site.department_id or 0)
    try:
        update_site(db, site, payload.model_dump(exclude_unset=True, exclude={"shares"}))
        if payload.shares is not None:
            set_site_shares(db, site, validate_shares(db, payload.shares))
    except GuardError as exc:
        raise _guard_error(exc)
    except SharesValidationError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=str(exc))
    log_action(db, actor, "guard_site", site.id, "update", after={"name": site.name})
    db.commit()
    db.refresh(site)
    return _site_read(site, _company_names(db))


@router.delete("/sites/{site_id}")
def remove_site(
    site_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    site = db.get(GuardSite, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Объект не найден")
    _access(db, actor, site.department_id or 0)
    result = delete_site(db, site)
    log_action(db, actor, "guard_site", site_id, "delete")
    db.commit()
    return {"result": result}


# ── Справочник: посты внутри объектов ─────────────────────────────────────────

@router.get("/posts", response_model=list[GuardPostRead])
def get_posts(
    db: Session = Depends(get_db), actor: Employee = Depends(get_current_user)
):
    _require_vahta(actor)
    posts = [_post_read(p) for p in list_posts(db, guard_department_ids(db, actor))]
    if not can_see_finances(actor):
        return [
            p.model_copy(update={"shift_rate": None, "effective_rate": None})
            for p in posts
        ]
    return posts


@router.post("/posts", response_model=GuardPostRead, status_code=status.HTTP_201_CREATED)
def post_post(
    payload: GuardPostCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    site = db.get(GuardSite, payload.site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Объект не найден")
    _access(db, actor, site.department_id or 0)
    try:
        post = create_post(db, payload.model_dump())
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(db, actor, "guard_post", post.id, "create", after={"name": post.name})
    db.commit()
    db.refresh(post)
    return _post_read(post)


@router.patch("/posts/{post_id}", response_model=GuardPostRead)
def patch_post(
    post_id: int,
    payload: GuardPostUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    post = db.get(GuardPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден")
    _access(db, actor, post.department_id or 0)
    data = payload.model_dump(exclude_unset=True)
    try:
        update_post(db, post, data)
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(db, actor, "guard_post", post.id, "update", after={"name": post.name})
    db.commit()
    db.refresh(post)
    return _post_read(post)


@router.delete("/posts/{post_id}")
def remove_post(
    post_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_settings(actor)
    post = db.get(GuardPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден")
    _access(db, actor, post.department_id or 0)
    result = delete_post(db, post)
    log_action(db, actor, "guard_post", post_id, "delete")
    db.commit()
    return {"result": result}


# ── Табель ────────────────────────────────────────────────────────────────────

@router.get("/{year}/{month}", response_model=GuardMonthRead)
def get_month(
    year: int,
    month: int,
    department_id: int | None = Query(default=None),
    #: Режим отображения: не задан — месяц целиком, 1 — дни 1–15, 2 — 16–конец.
    half: int | None = Query(default=None, ge=1, le=2),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_vahta(actor)
    return build_guard_month(db, actor, year, month, department_id, half)


def _assignment_or_404(db: Session, actor: Employee, assignment_id: int) -> GuardAssignment:
    assignment = db.get(GuardAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="Строка не найдена")
    _access(db, actor, assignment.department_id or 0)
    return assignment


def _editable_assignment_or_404(
    db: Session, actor: Employee, assignment_id: int
) -> GuardAssignment:
    """Строка для ПРАВКИ: доступ к отделу плюс незакрытый месяц. Все мутации
    назначения берут строку отсюда — проверку периода нельзя забыть по месту."""
    assignment = _assignment_or_404(db, actor, assignment_id)
    _require_open_month(db, assignment.department_id, assignment.year, assignment.month)
    return assignment


def _resolve_position(
    db: Session,
    actor: Employee,
    place,
    position_id: int | None,
    employee_id: int | None,
    rate: Decimal | None,
    year: int,
    month: int,
    job_title_id: int | None = None,
) -> EmployeePosition | None:
    """Рабочее место, которое встаёт на место работы охраны.

    Задан `position_id` — берём его. Задан только `employee_id` — человеку
    заводится ещё одно рабочее место под этот пост (совместительство: в образце
    один человек стоит на двух постах). Не задано ничего — пустой слот.
    """
    if position_id is not None:
        position = db.get(EmployeePosition, position_id)
        if position is None:
            raise HTTPException(status_code=404, detail="Рабочее место не найдено")
        return position
    if employee_id is None:
        return None
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    existing = [
        p
        for p in employee.active_positions
        if p.department_id == place.department_id
        and not _position_busy(db, p, place, year, month)
    ]
    if existing:
        return existing[0]
    # Дальше — НОВОЕ рабочее место в охране (task_stage2_access п.2.9). Заводит
    # его тот, кто ведёт штат охраны (admin, менеджер охраны), как в «Сотрудниках
    # охраны» и быстром найме. Табельщик ставит на пост только тех, у кого в этом
    # подразделении уже есть свободное место; раньше постановка и замена молча
    # заводили позицию и в обход прав.
    if actor.role not in _SETTINGS_ROLES:
        raise HTTPException(
            status_code=403,
            detail=(
                f"У «{employee.full_name}» нет свободного рабочего места в этом "
                "подразделении охраны. Завести новое (совместительство, перевод из "
                "другого подразделения) может администратор или менеджер охраны."
            ),
        )
    # Должность строки задаёт тип оплаты нового рабочего места: начальник,
    # поставленный на пост, заводится на окладе (task_guard_ownership).
    try:
        return add_position_for_guard(db, employee, place, rate, job_title_id)
    except GuardError as exc:
        raise _guard_error(exc)


def _position_busy(
    db: Session, position: EmployeePosition, place, year: int, month: int
) -> bool:
    """Стоит ли это рабочее место уже на ДРУГОМ месте вахты В ЭТОМ МЕСЯЦЕ.

    Каждый пост — своё рабочее место, иначе расчёт по двум постам не разделить
    (у Караулова в образце ставки 5 000 и 3 500 под одним табельным номером).

    Проверка ограничена месяцем НАМЕРЕННО. Без неё человек, переведённый между
    месяцами с поста A на пост B, получал бы каждый раз НОВОЕ рабочее место, а
    прежнее оставалось бы висеть в общей ведомости лишней строкой по нулям.
    """
    is_post = isinstance(place, GuardPost)
    same_place = (
        (GuardAssignment.post_id == place.id) if is_post
        else (GuardAssignment.crew_id == place.id)
    )
    return (
        db.query(GuardAssignment.id)
        .filter(
            GuardAssignment.position_id == position.id,
            ~same_place,
            GuardAssignment.year == year,
            GuardAssignment.month == month,
        )
        .first()
        is not None
    )


def _place_or_404(db: Session, actor: Employee, post_id, crew_id):
    """Место работы из запроса: пост объекта ЛИБО экипаж ГБР, ровно одно."""
    if (post_id is None) == (crew_id is None):
        raise HTTPException(
            status_code=422,
            detail="Укажите ровно одно место работы: пост объекта или экипаж ГБР",
        )
    place = db.get(GuardPost, post_id) if post_id is not None else db.get(GuardCrew, crew_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Место работы не найдено")
    _access(db, actor, place.department_id or 0)
    return place


@router.post("/assignments", status_code=status.HTTP_201_CREATED)
def post_assignment(
    payload: GuardAssignmentCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Поставить на место работы одного человека, нескольких сразу или пустой слот."""
    _require_timesheet_edit(actor)
    place = _place_or_404(db, actor, payload.post_id, payload.crew_id)
    _require_open_month(db, place.department_id, payload.year, payload.month)
    if payload.rate is not None:
        _require_money(actor)
    if payload.job_title_id is not None:
        try:
            get_job_title(db, payload.job_title_id)
        except GuardError as exc:
            raise _guard_error(exc)

    # Пакет: несколько человек на один пост. Пустой список — это не «поставить
    # никого», а ошибка ввода, поэтому 422, а не молчаливый пустой слот.
    if payload.employee_ids is not None:
        if not payload.employee_ids:
            raise HTTPException(status_code=422, detail="Не выбран ни один сотрудник")
        created: list[int] = []
        for employee_id in dict.fromkeys(payload.employee_ids):
            position = _resolve_position(
                db, actor, place, None, employee_id, payload.rate,
                payload.year, payload.month, payload.job_title_id,
            )
            try:
                assignment = create_assignment(
                    db, year=payload.year, month=payload.month, place=place,
                    position=position, rate=payload.rate, job_title_id=payload.job_title_id,
                )
            except GuardError as exc:
                raise _guard_error(exc)
            log_action(
                db, actor, "guard_assignment", assignment.id, "create",
                after={"place": place.name, "position_id": assignment.position_id},
            )
            created.append(assignment.id)
        # Один commit на весь пакет: половина поставленных людей хуже ошибки.
        db.commit()
        return {"ids": created, "created": len(created)}

    position = _resolve_position(
        db, actor, place, payload.position_id, payload.employee_id, payload.rate,
        payload.year, payload.month, payload.job_title_id,
    )
    try:
        assignment = create_assignment(
            db,
            year=payload.year,
            month=payload.month,
            place=place,
            position=position,
            rate=payload.rate,
            job_title_id=payload.job_title_id,
        )
    except GuardError as exc:
        # Сюда приходит и «позиция не охранная»: `position_id` из запроса может
        # указывать на любое рабочее место компании (task_stage1 п.1.4).
        raise _guard_error(exc)
    log_action(
        db, actor, "guard_assignment", assignment.id, "create",
        after={"place": place.name, "position_id": assignment.position_id},
    )
    db.commit()
    return {"id": assignment.id, "ids": [assignment.id], "created": 1}


@router.patch("/assignments/{assignment_id}")
def patch_assignment(
    assignment_id: int,
    payload: GuardAssignmentUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Правка строки: должность, ставка, премия, штраф, примечание.

    Должность и примечание — не деньги, их правит и табельщик; ставка, премия и
    штраф денежные, и на них стоит `_require_money`.

    **Официальной выплаты здесь нет** (task_guard_form_rate_official): она
    вычисляется из оф. зарплаты рабочего места, и ввести её руками нельзя ни с
    экрана, ни запросом — поля просто нет в схеме.
    """
    _require_timesheet_edit(actor)
    assignment = _editable_assignment_or_404(db, actor, assignment_id)
    data = payload.model_dump(exclude_unset=True)
    # Должность — не деньги: её правит и табельщик. Всё остальное в этой форме
    # денежное, поэтому финансовая проверка стоит на нём.
    if set(data) - {"job_title_id", "note"}:
        _require_money(actor)
    if data.get("job_title_id") is not None:
        try:
            get_job_title(db, data["job_title_id"])
        except GuardError as exc:
            raise _guard_error(exc)
    before = {k: str(getattr(assignment, k)) for k in data}
    for field, value in data.items():
        if value is not None or field == "note":
            setattr(assignment, field, value)
    log_action(
        db, actor, "guard_assignment", assignment.id, "update",
        before=before, after={k: str(v) for k, v in data.items()},
    )
    db.commit()
    return {"result": "ok"}


@router.delete("/assignments/{assignment_id}")
def remove_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_timesheet_edit(actor)
    assignment = _editable_assignment_or_404(db, actor, assignment_id)
    log_action(db, actor, "guard_assignment", assignment_id, "delete")
    delete_assignment(db, assignment)
    db.commit()
    return {"result": "deleted"}


@router.put("/day")
def put_day(
    payload: GuardDayInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Отметить или снять один день выхода."""
    _require_timesheet_edit(actor)
    assignment = _editable_assignment_or_404(db, actor, payload.assignment_id)
    try:
        toggle_day(db, assignment, payload.day, payload.value)
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(
        db, actor, "guard_assignment", assignment.id, "shift",
        after={"day": payload.day, "value": payload.value},
    )
    db.commit()
    return {"result": "ok"}


@router.put("/days")
def put_days(
    payload: GuardDaysInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """«Отметить все» / «снять все»: набор дней строки целиком."""
    _require_timesheet_edit(actor)
    assignment = _editable_assignment_or_404(db, actor, payload.assignment_id)
    set_days(db, assignment, set(payload.days))
    log_action(
        db, actor, "guard_assignment", assignment.id, "shifts",
        after={"days": sorted(set(payload.days))},
    )
    db.commit()
    return {"result": "ok"}


@router.post("/replace")
def post_replace(
    payload: GuardReplaceInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Замена на посту: дни с указанного числа уходят сменщику."""
    _require_timesheet_edit(actor)
    assignment = _editable_assignment_or_404(db, actor, payload.assignment_id)
    if payload.rate is not None:
        _require_money(actor)
    position = _resolve_position(
        db, actor, assignment.place, payload.position_id, payload.employee_id, payload.rate,
        assignment.year, assignment.month, assignment.job_title_id,
    )
    try:
        kept, successor = replace_on_post(
            db, assignment, position=position,
            from_day=payload.from_day, rate=payload.rate,
        )
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(
        db, actor, "guard_assignment", assignment.id, "replace",
        after={"from_day": payload.from_day, "successor": successor.id if successor else None},
    )
    db.commit()
    return {
        "assignment_id": kept.id,
        "successor_id": successor.id if successor else None,
    }


@router.post("/{year}/{month}/copy-previous")
def post_copy_previous(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Скопировать состав, посты и ставки предыдущего месяца."""
    _require_vahta(actor)
    _require_settings(actor)
    department_ids = guard_department_ids(db, actor)
    for department_id in department_ids:
        _require_open_month(db, department_id, year, month)
    try:
        copied = copy_previous_period(db, year, month, department_ids)
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(
        db, actor, "guard_period", 0, "copy",
        after={"year": year, "month": month, "rows": copied},
    )
    db.commit()
    return {"copied": copied}


# ── Быстрый найм и кандидаты ──────────────────────────────────────────────────

@router.get("/similar", response_model=list[GuardSimilarEmployee])
def get_similar(
    full_name: str = Query(min_length=2),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Похожие сотрудники по ФИО — чтобы срочный найм не плодил дубли."""
    _require_vahta(actor)
    return [
        GuardSimilarEmployee(
            id=e.id,
            full_name=e.full_name,
            tab_number=e.tab_number,
            department_name=e.department.name if e.department else None,
            is_active=e.is_active,
        )
        for e in find_similar_employees(db, full_name)
    ]


@router.post("/quick-hire", status_code=status.HTTP_201_CREATED)
def post_quick_hire(
    payload: GuardQuickHireInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Оформить нового охранника: ФИО, пост, ставка."""
    _require_vahta(actor)
    _require_settings(actor)
    place = _place_or_404(db, actor, payload.post_id, payload.crew_id)
    # Проверка ДО найма: иначе в закрытом месяце остался бы заведённый сотрудник
    # без постановки на пост.
    if payload.assign and payload.year and payload.month:
        _require_open_month(db, place.department_id, payload.year, payload.month)
    try:
        employee, position = quick_hire(
            db, full_name=payload.full_name, place=place, rate=payload.rate,
            job_title_id=payload.job_title_id,
        )
    except GuardError as exc:
        raise _guard_error(exc)

    assignment_id = None
    if payload.assign and payload.year and payload.month:
        try:
            assignment = create_assignment(
                db, year=payload.year, month=payload.month, place=place,
                position=position, rate=payload.rate, job_title_id=payload.job_title_id,
            )
        except GuardError as exc:
            raise _guard_error(exc)
        assignment_id = assignment.id
    log_action(
        db, actor, "employee", employee.id, "create",
        after={"full_name": employee.full_name, "tab_number": employee.tab_number,
               "source": "vahta_quick_hire"},
    )
    db.commit()
    return {
        "employee_id": employee.id,
        "position_id": position.id,
        "tab_number": employee.tab_number,
        "assignment_id": assignment_id,
    }


def _place_label(assignment: GuardAssignment) -> str:
    """Где человек стоит: у поста — «Объект · Пост», у экипажа — его имя."""
    if assignment.post is not None:
        site = assignment.post.site
        return f"{site.name} · {assignment.post.name}" if site else assignment.post.name
    return assignment.crew.name if assignment.crew else ""


@router.get("/{year}/{month}/candidates", response_model=list[GuardCandidateRead])
def get_candidates(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Кандидаты для окон «Поставить на пост» и «Кто сменит на посту».

    Двумя группами, как в прототипе: занятые (с пометкой, где сейчас стоят) и
    свободные — те, кто в этом месяце нигде не стоит.

    **Строка на ЧЕЛОВЕКА, а не на рабочее место.** Раньше отдавалась строка на
    позицию, и человек с двумя постами появлялся в списке дважды одинаковым
    именем — выбрать из такого нельзя. Позиция вызывающему и не нужна: и
    постановка на пост, и замена передают `employee_id`, а рабочее место под
    нужный пост подбирает сам сервер (`_resolve_position`).
    """
    _require_vahta(actor)
    department_ids = guard_department_ids(db, actor)
    busy: dict[int, list[str]] = {}
    for assignment in list_assignments(db, year, month, department_ids):
        if assignment.position is None or assignment.position.employee is None:
            continue
        label = _place_label(assignment)
        where = busy.setdefault(assignment.position.employee_id, [])
        if label not in where:
            where.append(label)

    positions = (
        db.query(EmployeePosition)
        .filter(
            EmployeePosition.department_id.in_(department_ids or [0]),
            EmployeePosition.is_active == True,  # noqa: E712
        )
        .all()
    )
    seen: set[int] = set()
    result: list[GuardCandidateRead] = []
    for position in positions:
        employee = position.employee
        if employee is None or not employee.is_active or employee.is_system_admin:
            continue
        if employee.id in seen:
            continue
        seen.add(employee.id)
        result.append(
            GuardCandidateRead(
                employee_id=employee.id,
                position_id=position.id,
                full_name=employee.full_name,
                tab_number=employee.tab_number,
                where=" · ".join(busy.get(employee.id, [])) or None,
            )
        )
    result.sort(key=lambda c: (c.where is not None, c.full_name))
    return result


# ── Выгрузка ──────────────────────────────────────────────────────────────────

@router.get("/{year}/{month}/export/excel")
def export_excel(
    year: int,
    month: int,
    department_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Табель вахты в Excel по образцу заказчика — один лист на месяц."""
    _require_vahta(actor)
    _require_money(actor)
    month_data = build_guard_month(db, actor, year, month, department_id)
    content = generate_guard_timesheet_excel(db, month_data)
    log_action(db, actor, "guard_period", 0, "export", after={"year": year, "month": month})
    db.commit()
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="vahta_{year}_{month:02d}.xlsx"'
        },
    )


# ── Сотрудники охраны (task_guard_ownership) ──────────────────────────────────
#
# Штат охраны ведётся ЗДЕСЬ: рабочее место в охранном подразделении общий
# справочник не правит (403 на бэке). Человек при этом один на всю систему —
# ФИО, таб. №, доступ и его даты работы в компании остаются в справочнике.
# Доступ к экрану — администратор и менеджер охраны, как у настроек вахты.


def _staff_read(
    position: EmployeePosition,
    places: dict[int, list[str]],
    titles: dict[str, GuardJobTitle],
    with_money: bool = True,
) -> GuardStaffRead:
    employee = position.employee
    # `titles` — справочник одним запросом на весь список (ревью: было по два
    # запроса на строку). Позиция без ссылки узнаётся по названию один раз.
    title = job_title_of_position(position, titles)
    return GuardStaffRead(
        employee_id=employee.id,
        position_id=position.id,
        full_name=employee.full_name,
        tab_number=employee.tab_number,
        department_id=position.department_id,
        department_name=position.department.name if position.department else "",
        job_title_id=title.id if title else None,
        job_title_name=title.name if title else (position.title or ""),
        pay_type=position.pay_type,
        hire_date=position.hire_date,
        dismissal_date=position.dismissal_date,
        employee_is_active=employee.is_active,
        places=places.get(position.id, []),
        # Признак — свойство рабочего места, а не месяца. Зарплата — деньги:
        # тем, кто их не видит, приходит None, как остальные суммы вахты.
        is_official=bool(position.is_official),
        official_salary=position.official_salary if with_money else None,
    )


def _staff_month_context(
    db: Session, department_ids: list[int], year: int | None, month: int | None
) -> dict[int, list[str]]:
    """Где стоит каждое рабочее место в выбранном месяце.

    Официальное трудоустройство сюда не входит: это свойство самого рабочего
    места, а не месяца (task_guard_form_rate_official).
    """
    places: dict[int, list[str]] = {}
    if year is None or month is None:
        return places
    for assignment in list_assignments(db, year, month, department_ids):
        if assignment.position_id is None:
            continue
        labels = places.setdefault(assignment.position_id, [])
        label = _place_label(assignment)
        if label not in labels:
            labels.append(label)
    return places


def _staff_position_or_404(
    db: Session, actor: Employee, position_id: int
) -> EmployeePosition:
    position = db.get(EmployeePosition, position_id)
    if position is None or not is_guard_position(db, position):
        raise HTTPException(status_code=404, detail="Сотрудник охраны не найден")
    _access(db, actor, position.department_id)
    return position


@router.get("/staff", response_model=list[GuardStaffRead])
def get_staff(
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
    department_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Сотрудники охраны: строка на рабочее место в охранном подразделении."""
    _require_vahta(actor)
    _require_settings(actor)
    department_ids = guard_department_ids(db, actor)
    if department_id is not None:
        _access(db, actor, department_id)
        department_ids = [department_id]
    places = _staff_month_context(db, department_ids, year, month)
    titles = title_index(db)
    with_money = can_see_finances(actor)
    return [
        _staff_read(p, places, titles, with_money)
        for p in list_staff_positions(db, department_ids)
    ]


@router.post("/staff", response_model=GuardStaffRead, status_code=status.HTTP_201_CREATED)
def post_staff(
    payload: GuardStaffCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Оформить сотрудника охраны: человек с рабочим местом в охране.

    Суммы у охранного места нет (task_guard_form_rate_official): способ оплаты
    берётся от должности, а цена смены или оклад стоят в строке табеля.
    """
    _require_vahta(actor)
    _require_settings(actor)
    _access(db, actor, payload.department_id)
    try:
        employee, position = create_staff(
            db,
            full_name=payload.full_name,
            tab_number=payload.tab_number,
            department_id=payload.department_id,
            job_title_id=payload.job_title_id,
            hire_date=payload.hire_date,
            dismissal_date=payload.dismissal_date,
            is_official=payload.is_official,
            official_salary=payload.official_salary,
        )
    except GuardError as exc:
        raise _guard_error(exc)
    log_action(
        db, actor, "employee", employee.id, "create",
        after={"full_name": employee.full_name, "tab_number": employee.tab_number,
               "position_id": position.id, "source": "vahta_staff"},
    )
    db.commit()
    db.refresh(position)
    return _staff_read(position, {}, title_index(db), can_see_finances(actor))


@router.patch("/staff/{position_id}", response_model=GuardStaffRead)
def patch_staff(
    position_id: int,
    payload: GuardStaffUpdate,
    confirm: bool = Query(
        False,
        description=(
            "Подтвердить перевод из охраны в обычное подразделение или снятие "
            "признака «официально устроен» с начисленной выплатой"
        ),
    ),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Правка рабочего места охраны. Перевод в обычное подразделение без
    `confirm` отвечает 409 с причинами, по которым место не войдёт в расчёт."""
    _require_vahta(actor)
    _require_settings(actor)
    position = _staff_position_or_404(db, actor, position_id)
    data = payload.model_dump(exclude_unset=True)
    before = {
        "department_id": position.department_id,
        "title": position.title,
        "pay_type": position.pay_type,
        "amount": str(amount_of(position)) if amount_of(position) is not None else None,
        "hire_date": str(position.hire_date) if position.hire_date else None,
        "dismissal_date": str(position.dismissal_date) if position.dismissal_date else None,
        "is_official": position.is_official,
        "official_salary": (
            str(position.official_salary) if position.official_salary is not None else None
        ),
    }
    try:
        warning = update_staff(db, actor, position, data, confirm)
    except GuardError as exc:
        db.rollback()
        raise _guard_error(exc)
    if isinstance(warning, OfficialRemovalWarning):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "guard_official_removal_confirmation_required",
                "message": (
                    "У рабочего места уже начислена официальная выплата — "
                    "после снятия признака она и налог станут нулевыми"
                ),
                "months": [
                    {"year": year, "month": month, "amount": str(amount)}
                    for year, month, amount in warning.months
                ],
                "total": str(warning.total),
            },
        )
    if warning is not None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "guard_transfer_out_confirmation_required",
                "message": (
                    f"Рабочее место переходит в «{warning.department_name}» и "
                    "дальше ведётся в общем справочнике"
                ),
                "department_name": warning.department_name,
                "issues": warning.issues,
                # Перевод гасит «официально устроен»: выплата и налог этих
                # месяцев станут нулевыми — подтверждение должно назвать суммы.
                "official_months": [
                    {"year": year, "month": month, "amount": str(amount)}
                    for year, month, amount in warning.official_months
                ],
            },
        )
    log_action(
        db, actor, "employee_position", position.id, "update",
        before=before, after=jsonable_encoder({**data, "source": "vahta_staff"}),
    )
    db.commit()
    db.refresh(position)
    return _staff_read(position, {}, title_index(db), can_see_finances(actor))
