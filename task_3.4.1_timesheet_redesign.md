# Задача 3.4.1 — Переделка вёрстки страницы табеля по референсу

## Контекст

Текущая реализация страницы табеля (`frontend/src/pages/TimesheetPage.tsx`) имеет проблемы с вёрсткой:
- Колонки слева налагаются на сайдбар
- Sticky-колонки справа наезжают друг на друга
- Скроллится вся страница, а не только средняя часть таблицы
- Многострочное отображение сотрудника (N строк на компании) усложняет вёрстку настолько что обычные подходы ломаются

Есть **рабочий референс** с правильной архитектурой UI: пользователь загрузил его в виде HTML-файла `docs/tabel_portal_reference.html` (положу в проект отдельно). Эта задача — переделать `TimesheetPage.tsx` по образцу из этого референса.

## Ключевое архитектурное изменение

**Сотрудник — одна строка в таблице.** Не N строк по компаниям как сейчас, а **одна** строка, а компании выбираются внутри ячейки дня.

Структура одного дня для сотрудника:
```
┌─────────────────┐
│ [А ▾]  8.0  [×] │  ← слот компании A с часами
│ [Б ▾]  4.0  [×] │  ← слот компании B с часами
│ + добавить       │  ← кнопка добавить слот
└─────────────────┘
```

То есть в ячейке `(сотрудник × день)` может быть несколько слотов. Каждый слот — `(компания, часы)`. Слотов столько, на скольких компаниях работал сотрудник в этот день.

Это полностью убирает потребность в multi-row и упрощает sticky колонки до тривиальных.

## Что нужно сделать

### Структура страницы

```
<div class="overflow-x-auto rounded-lg border">
  <table class="border-collapse text-xs min-w-max">
    <thead>
      <tr>
        <th class="sticky left-0 top-0 z-30">Сотрудник</th>  ← sticky угол
        <th class="sticky top-0 z-20">Отдел</th>             ← sticky шапка
        <th class="sticky top-0 z-20">График</th>
        <th class="sticky top-0 z-20">1 Пн</th>              ← дни 1..31 в шапке
        ... 
        <th class="sticky top-0 z-20">Итого ч</th>
        <th class="sticky top-0 z-20">Норма</th>             ← финансы (только admin/accountant)
        <th class="sticky top-0 z-20">Δ</th>
        <th class="sticky top-0 z-20">Оклад</th>
        <th class="sticky top-0 z-20">Сверхур.</th>
        <th class="sticky top-0 z-20">Праздн.</th>
        <th class="sticky top-0 z-20">Итого ₽</th>
      </tr>
    </thead>
    <tbody>
      <!-- одна строка на сотрудника -->
      <tr>
        <td class="sticky left-0 z-10 bg-white">Бублий А.В.</td>
        <td>ИТО</td>
        <td>5/2</td>
        <!-- 31 ячейка дней -->
        <td>
          <div class="flex flex-col gap-1">
            <div class="slot">[А] 8.0 [×]</div>
            <div class="slot">[Б] 4.0 [×]</div>
            <button class="slot-add">+</button>
          </div>
        </td>
        ...
        <!-- финансовые итоги -->
        <td>170</td>
        <td>167</td>
        <td class="text-amber">+3</td>
        <td>350 000 ₽</td>
        <td>9 431 ₽</td>
        <td>1 048 ₽</td>
        <td class="font-bold">360 479 ₽</td>
      </tr>
      
      <!-- footer строка ИТОГО -->
      <tr class="bg-gray-100 font-bold">
        <td class="sticky left-0">ИТОГО</td>
        <td></td>
        <td></td>
        <!-- суммы часов по дням -->
        <td>8</td><td>12</td>...
        <td>170</td>
        <td>167</td>
        <td>+3</td>
        <td>350 000 ₽</td>
        ...
      </tr>
    </tbody>
  </table>
</div>
```

### CSS правила (Tailwind)

**Скролл-контейнер:**
```jsx
<div className="overflow-x-auto rounded-lg border border-gray-200">
  <table className="border-collapse text-xs">
    {/* table content with min-width: max-content */}
```

Таблица сама занимает min-width: max-content (естественный размер). Контейнер `overflow-x-auto` создаёт скролл когда таблица шире viewport.

**Sticky левая колонка (Сотрудник):**
```jsx
<th className="sticky left-0 z-30 bg-gray-50 min-w-[180px]">Сотрудник</th>  // в шапке
<td className="sticky left-0 z-10 bg-white min-w-[180px]">{employee.full_name}</td>  // в теле
```

**z-index стек (важно!):**
- `z-30` — угол (sticky слева + top одновременно)
- `z-20` — шапка строки (`thead th` с top-0)
- `z-10` — sticky колонка в теле (`tbody td` с left-0)
- default — обычные ячейки

**ВАЖНО про сайдбар приложения:** sidebar должен иметь z-index выше чем sticky-колонки таблицы — `z-40` или `z-50`. Это решает проблему «таблица выходит на сайдбар».

### Слоты внутри ячейки дня

Каждый слот — небольшой блок с цветом компании:

```jsx
<div className="flex flex-col gap-1 p-1">
  {slots.map((slot, i) => (
    <div 
      key={i} 
      className="flex items-center gap-1 px-1 py-0.5 rounded text-[11px]"
      style={{ backgroundColor: companyColor(slot.company_id).bg, color: companyColor(slot.company_id).color }}
    >
      <select 
        value={slot.company_id}
        onChange={...}
        className="bg-transparent border-0 outline-none w-7 text-xs font-mono"
      >
        {companies.map(c => <option value={c.id}>{c.code}</option>)}
      </select>
      <input 
        type="number" 
        value={slot.hours}
        onChange={...}
        className="w-7 bg-transparent border-0 outline-none text-center font-mono"
        min={0} max={24} step={0.5}
      />
      <button onClick={() => deleteSlot(...)} className="opacity-40 hover:opacity-100">×</button>
    </div>
  ))}
  {!isWeekend && (
    <button onClick={addSlot(...)} className="text-[10px] text-gray-400 border border-dashed rounded px-1 hover:text-blue-600">
      +
    </button>
  )}
</div>
```

### Финансовые колонки

В **той же строке** сотрудника, после всех дней — несколько колонок с финансами. Берутся из `payroll.employees[emp.id]`:

| Колонка | Содержимое | Видимость |
|---|---|---|
| Итого ч | total_hours | все |
| Норма | norm_hours | admin/accountant |
| Δ | delta_hours (цвет: + = янтарь, − = красный) | admin/accountant |
| Оклад | base_amount | admin/accountant |
| Сверхур. | overtime_amount | admin/accountant |
| Праздн. | holiday_amount | admin/accountant |
| Итого ₽ | total_amount (жирно, синий) | admin/accountant |

Если `is_calculable=false` — в финансовых ячейках «—», cursor:help, title-tooltip с reason.

**Никакого rowspan, никакого breakdown по компаниям в строке табеля.** Per-company breakdown переезжает на отдельную страницу `/admin/payroll` где это удобнее показать (как уже есть).

### Footer строка ИТОГО

После всех сотрудников — строка с суммами:
- ФИО → «ИТОГО» (sticky-left, серый фон)
- По каждому дню — сумма часов всех сотрудников
- Итого ч — общая сумма
- Норма, Δ, Оклад, Сверхур., Праздн., Итого ₽ — соответствующие агрегаты из `payroll.total_*` (admin/accountant only)

### Цвета компаний

Сохранить функцию `companyColor(companyId, allCompanies): {bg, color}` из текущего кода. Палитра из 8-10 цветов, циклом по index в массиве компаний.

В заголовке страницы добавить **легенду** — маленькая полоска под header:
```
[● А — ООО Альфа] [● Б — ООО Бета] [● В — ООО Гамма]   Серый = выходной · «+» = добавить слот
```

### Что НЕ менять

- Структура API запросов остаётся как есть (`getMonth`, `saveCell`, `getPayroll`)
- Логика autofill, dismiss/rehire, статусы периодов
- Сайдбар приложения (только проверить z-index)
- Бэкенд вообще не трогать (всё уже работает)

### Адаптация под текущие данные

В нашем API `entries: TimesheetEntry[]` — массив записей `(employee_id, work_date, company_id, hours)`. В рендере нужно собрать структуру `cellsByEmployeeAndDay: Map<empId, Map<day, TimesheetEntry[]>>` и потом для каждой ячейки итерировать массив записей как слоты.

При сохранении ячейки — текущий API `saveCell({employee_id, work_date, company_id, hours})` подходит идеально. `hours=0` удаляет запись (= удаление слота).

При смене компании в существующем слоте — это два запроса: удалить старую (hours=0 на старую company) + создать новую. Либо можно сделать атомарный update — но проще сначала так, если будут глюки — переделаем.

### Кнопка «+ компания» в колонке сотрудника

Эту кнопку из текущей реализации — **убрать**. Она больше не нужна, потому что компания выбирается прямо в ячейке дня через select слота. Список расширяемых компаний больше не существует — все компании всегда доступны через select в любом слоте.

То же касается `extra_companies_by_employee` в ответе бэкенда — пусть остаётся для совместимости, фронт его просто игнорирует.

## Acceptance criteria

После выполнения в браузере:

1. **Один сотрудник = одна строка** в таблице
2. **В ячейке дня** видны слоты (если есть часы), каждый со своим цветом компании
3. **Кнопка `+`** в ячейке добавляет новый слот (по умолчанию следующая неиспользованная компания)
4. **Слот можно удалить** через `×`
5. **Смена компании** в слоте через select работает (без перезагрузки страницы)
6. **Скролл только в таблице** (горизонтальный). Страница в целом не двигается влево-вправо.
7. **Sticky колонки**: «Сотрудник» слева, шапка дней сверху — остаются на месте при скролле
8. **Сайдбар чистый** — таблица не выходит за свой контейнер
9. **Финансовые колонки** (Норма / Δ / Оклад / Сверхур. / Праздн. / Итого ₽) — справа, видны только admin/accountant
10. **Footer ИТОГО** — внизу таблицы, sticky bottom если возможно (опционально, без обязательства)

Сравни с референсом — он в файле `docs/tabel_portal_reference.html` в проекте. Открой его в браузере (просто двойной клик) — посмотри как должна выглядеть таблица. Не нужно копировать весь дизайн, нужна архитектура (single-row per employee, slots inside cells, single overflow container).

## Подводные камни

- **z-index sidebar**: убедись что сайдбар приложения имеет z-index >= 40, иначе sticky-таблица перекроет
- **Tabular переключение даты**: при смене месяца — полная перезагрузка данных, не оставлять старые слоты
- **Performance**: 30+ сотрудников × 31 день × до 3 слотов = до 3000 select-ов. Это много, но React справится. Если будут тормоза — мемоизация колонок через React.memo
- **Backwards compat бэкенда**: ничего не меняй на бэке, только фронт. `extra_companies_by_employee` пусть остаётся в ответе, просто не используется
- **Calendar тип дня**: подсветка дней (праздник красный, сокращённый жёлтый) — из `calendarSummary.days` от endpoint `/api/calendar/{year}/{month}/summary`. Если нет — fallback на дни недели (Сб/Вс серым)

## Что НЕ делать

- Не переделывай страницу `/admin/payroll` (per-company breakdown), она остаётся как есть
- Не трогай дашборд / справочники
- Не меняй модели на бэкенде
- Не делай drag&drop, multi-select ячеек и прочие фичи — только базовая навигация Tab/Enter в инпутах
- Не делай свёрнутый / развёрнутый режим — табель один, как описано

## В конце

1. Скрин страницы табеля в браузере с заполненными часами одного сотрудника на 2 компании в разные дни
2. Скрин при скролле вправо — sticky-колонка «Сотрудник» осталась на месте
3. Скрин под manager (без финансовых колонок)
4. Запушить как `refactor(frontend): timesheet redesign — single-row-per-employee with company slots`
