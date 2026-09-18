import { useEffect, useState } from 'react'

import { listGuardJobTitles } from '../api/vahta'
import type { GuardJobTitle } from '../types/api'

/**
 * Справочник должностей охраны для выпадашек (табель вахты, найм, штат).
 * Должности — строки справочника, а не константа: своей копии списка на
 * экране быть не должно. Активные, в настроенном порядке.
 */
export function useGuardJobTitles(): GuardJobTitle[] {
  const [titles, setTitles] = useState<GuardJobTitle[]>([])
  useEffect(() => {
    listGuardJobTitles().then(setTitles).catch(() => setTitles([]))
  }, [])
  return titles
}

/** Должность по умолчанию для вида места; ни одной помеченной — первая. */
export function defaultJobTitleId(
  titles: GuardJobTitle[],
  placeKind: 'post' | 'crew',
): number | undefined {
  const flag = placeKind === 'crew' ? 'default_for_crew' : 'default_for_post'
  return (titles.find((t) => t[flag]) ?? titles[0])?.id
}
