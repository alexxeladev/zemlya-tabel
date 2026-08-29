// frontend/src/components/ColumnFilter.tsx
// Фильтр в ЗАГОЛОВКЕ колонки, как в Excel (task_pilot_ux ч.2).
//
// До этого фильтры табеля жили над таблицей: поиск по ФИО/таб.№ и выбор
// юрлица. На пилоте (60–70 человек) этого не хватало — нужно было отобрать
// должность или график, а поля для этого не было вовсе.
//
// Кнопка-воронка стоит в <th>; список значений существует ТОЛЬКО пока
// поповер открыт (та же причина, по которой из ячеек убрали <select>:
// <option> — самый дорогой для браузера узел, а колонок-фильтров пять).
// Сам поповер — position:fixed поверх страницы: <th> сидит в контейнере с
// overflow-auto и обрезал бы выпадашку.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/** Значение пустой ячейки (нет таб.№, нет отдела) — отдельным пунктом списка. */
export const EMPTY_VALUE = '';

type Props = {
  /** Подпись колонки — она же заголовок поповера */
  label: string;
  /**
   * Значения, присутствующие в текущем табеле (уже с учётом ОСТАЛЬНЫХ
   * фильтров) — то есть список сужается вместе с таблицей, как в Excel.
   */
  options: string[];
  /** Отмеченные значения; пустой массив = фильтра нет (видно всё) */
  selected: string[];
  onChange: (values: string[]) => void;
  /** Как показывать пустое значение */
  emptyLabel?: string;
};

export function ColumnFilter({
  label, options, selected, onChange, emptyLabel = '(пусто)',
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const btnRef = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const popRef = useRef<HTMLDivElement>(null);

  /** Поставить поповер под свою кнопку, не вылезая за края окна. */
  const place = useCallback(() => {
    const btn = btnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const left = Math.max(4, Math.min(r.left, window.innerWidth - 264));
    const top = Math.max(4, Math.min(r.bottom + 6, window.innerHeight - 340));
    setPos({ top, left });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    /**
     * Прокрутка. Поповер — position:fixed, поэтому за уехавшим заголовком он
     * сам не пойдёт: его надо переставить.
     *
     * Важно: слушатель стоит в фазе ПЕРЕХВАТА (иначе прокрутка внутреннего
     * контейнера таблицы до window не всплывёт), и поэтому ловит в том числе
     * прокрутку СОБСТВЕННОГО списка значений. Раньше он на любую прокрутку
     * закрывался — колесом или полосой список пролистать было невозможно.
     * Прокрутку внутри поповера пропускаем мимо.
     */
    const onScroll = (e: Event) => {
      const target = e.target as Node | null;
      if (target && popRef.current?.contains(target)) return;
      const r = btnRef.current?.getBoundingClientRect();
      // Кнопка уехала из видимой части окна — держать список не за что.
      if (!r || r.bottom < 0 || r.top > window.innerHeight
        || r.right < 0 || r.left > window.innerWidth) {
        setOpen(false);
        return;
      }
      place();
    };
    // Ссылка на обработчик одна: инлайн-стрелка в removeEventListener —
    // другая функция, и слушатель остался бы висеть навсегда.
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', place);
    };
  }, [open, place]);

  const active = selected.length > 0;
  const selectedSet = new Set(selected);
  const needle = query.trim().toLocaleLowerCase('ru');
  const shown = needle
    ? options.filter((v) => (v || emptyLabel).toLocaleLowerCase('ru').includes(needle))
    : options;

  /** Отметить всё, что сейчас в списке. Без запроса это то же, что «фильтра
   *  нет», — и снимаем его совсем: так новые значения (другой месяц, другой
   *  отдел) не окажутся скрытыми давним «выбрал всё». */
  const applyShown = () => {
    if (!needle) { onChange([]); setOpen(false); return; }
    onChange(Array.from(new Set([...selected, ...shown])));
    setOpen(false);
  };

  const toggle = (value: string) => {
    // Клик по чекбоксу ВСЕГДА меняет его состояние. Соблазн свернуть «отмечено
    // всё» в «фильтра нет» пришлось отбросить: в колонке с единственным
    // значением такой клик не давал ничего видимого — галочка не вставала, и
    // фильтр выглядел сломанным. Снимает фильтр «Сбросить».
    onChange(
      selectedSet.has(value)
        ? selected.filter((v) => v !== value)
        : [...selected, value]
    );
  };

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={(e) => { e.stopPropagation(); setQuery(''); setOpen((v) => !v); }}
        title={
          active
            ? `${label}: выбрано ${selected.length} — ${selected.map((v) => v || emptyLabel).join(', ')}`
            : `Фильтр по колонке «${label}»`
        }
        className={
          'ml-1 inline-flex items-center gap-0.5 rounded px-1 align-middle leading-none '
          + (active
            ? 'bg-blue-600 text-white'
            : 'text-gray-400 hover:bg-gray-200 hover:text-gray-700')
        }
      >
        <svg viewBox="0 0 16 16" className="h-3 w-3" fill="currentColor" aria-hidden>
          <path d="M1.5 2.5h13l-5 6v4.2l-3 1.3V8.5l-5-6z" />
        </svg>
        {active && <span className="text-[9px] font-semibold">{selected.length}</span>}
      </button>

      {open && pos && (
        <>
          <div className="fixed inset-0 z-[99]" onClick={() => setOpen(false)} />
          <div
            ref={popRef}
            className="fixed z-[100] w-[260px] rounded border border-gray-200 bg-white shadow-lg"
            style={{ top: pos.top, left: pos.left }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-b border-gray-100 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
              {label}
            </div>

            {/* Текстовый ввод есть ВСЕГДА, а не только у длинных списков:
                набрать «Инж» быстрее, чем искать галочку глазами. Enter
                отмечает всё найденное — это и есть фильтр «содержит текст». */}
            <div className="px-2 pt-2">
              <input
                autoFocus
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== 'Enter') return;
                  e.preventDefault();
                  applyShown();
                }}
                placeholder="Введите текст и Enter…"
                className="w-full rounded border border-gray-300 px-2 py-1 text-xs font-normal focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <div className="flex gap-2 px-3 py-1.5 text-[11px] font-normal">
              <button
                type="button"
                className="text-blue-600 hover:underline"
                onClick={applyShown}
              >
                {needle ? `Выбрать найденные (${shown.length})` : 'Выбрать все'}
              </button>
              <button
                type="button"
                className="text-gray-500 hover:underline disabled:opacity-40"
                disabled={!active}
                onClick={() => onChange([])}
              >
                Сбросить
              </button>
            </div>

            <div className="max-h-[220px] overflow-y-auto border-t border-gray-100 py-1">
              {shown.length === 0 && (
                <div className="px-3 py-2 text-xs font-normal text-gray-400">
                  Ничего не найдено
                </div>
              )}
              {shown.map((value) => (
                <label
                  key={value || '__empty__'}
                  className="flex cursor-pointer items-center gap-2 px-3 py-1 text-xs font-normal hover:bg-blue-50"
                >
                  <input
                    type="checkbox"
                    className="h-3 w-3"
                    checked={selectedSet.has(value)}
                    onChange={() => toggle(value)}
                  />
                  <span className={'truncate ' + (value ? '' : 'italic text-gray-400')}>
                    {value || emptyLabel}
                  </span>
                </label>
              ))}
            </div>
          </div>
        </>
      )}
    </>
  );
}
