// frontend/src/components/RowCheck.tsx
// Личная отметка «строку проверил» (task_pilot_ux ч.3): чекбокс строки и
// счётчик прогресса. Оба подписаны на `store/rowChecks` НАПРЯМУЮ, поэтому
// клик перерисовывает только их, а не весь табель — см. комментарий в сторе.

import { memo } from 'react';
import { useRowChecksStore } from '../store/rowChecks';

type BoxProps = {
  positionId: number;
  /** сохранение на сервере; должен быть стабильным (useCallback) */
  onToggle: (positionId: number, value: boolean) => void;
};

/**
 * Класс `js-row-check` — не для стилей, а зацепка для CSS-подсветки строки
 * (`index.css`). Подсветка идёт от `:checked` самого инпута, поэтому строка
 * зеленеет без участия React.
 */
export const RowCheckBox = memo(function RowCheckBox({ positionId, onToggle }: BoxProps) {
  const checked = useRowChecksStore((s) => s.checked.has(positionId));
  // Синтетическая позиция (бэк не отдал ни одной) — отмечать нечего.
  if (!positionId) return null;
  return (
    <input
      type="checkbox"
      className="js-row-check h-3.5 w-3.5 cursor-pointer accent-emerald-600"
      checked={checked}
      onChange={(e) => onToggle(positionId, e.target.checked)}
      title={
        checked
          ? 'Проверено (вашей отметкой). Снять'
          : 'Отметить строку проверенной — отметка личная и своя у каждого месяца'
      }
    />
  );
});

type ProgressProps = {
  /** рабочие места видимых строк: счётчик считается по СТРОКАМ, не по людям */
  positionIds: number[];
};

export function RowCheckProgress({ positionIds }: ProgressProps) {
  const done = useRowChecksStore((s) => {
    let n = 0;
    for (const id of positionIds) if (s.checked.has(id)) n += 1;
    return n;
  });
  const total = positionIds.length;
  if (total === 0) return null;
  return (
    <span
      className={
        'text-xs whitespace-nowrap rounded px-2 py-0.5 '
        + (done === total
          ? 'bg-emerald-100 text-emerald-800 font-medium'
          : 'bg-gray-100 text-gray-600')
      }
      title={
        'Ваша личная отметка проверки: другие пользователи её не видят,'
        + ' в новом месяце все строки снова не отмечены.'
        + ' Считается по строкам — у совместителя их несколько.'
      }
    >
      Проверено {done} из {total}
    </span>
  );
}
