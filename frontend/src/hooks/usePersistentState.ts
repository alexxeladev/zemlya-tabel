import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'

import { loadValidated, saveUiState } from '../utils/persist'

/**
 * useState, переживающий переход между разделами и перезагрузку страницы
 * (task_ux_improvements ч.3): значение читается из localStorage при первом
 * рендере и записывается туда при каждом изменении.
 *
 * `isValid` — проверка формы сохранённого значения: в хранилище может лежать
 * значение от прежней версии экрана, и без проверки оно приехало бы в компонент.
 * Не прошло проверку или хранилище недоступно — берётся `fallback`.
 *
 * Запись идёт эффектом, а не внутри сеттера: сеттер обязан остаться чистым
 * (React вызывает функцию-обновление дважды в StrictMode).
 *
 * Только для настроек ВИДА (период, фильтры, развёрнутость блоков). Данные и
 * что-либо чувствительное здесь хранить нельзя.
 */
export function usePersistentState<T>(
  key: string,
  fallback: T,
  isValid: (value: unknown) => boolean = () => true,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => loadValidated(key, fallback, isValid))

  useEffect(() => {
    saveUiState(key, value)
  }, [key, value])

  return [value, setValue]
}
