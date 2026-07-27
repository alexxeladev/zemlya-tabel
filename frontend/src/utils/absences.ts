// Виды отсутствий и их оформление — единый источник для табеля (ячейки,
// легенда), вида «по компаниям» и ведомости. Коды совпадают с бэком
// (app/models/employee_absences.py) и формой Т-13.

export type AbsenceKind = 'vacation' | 'unpaid' | 'sick' | 'absent'

export interface AbsenceMeta {
  kind: AbsenceKind
  code: string
  label: string
  bg: string
  color: string
  paid: boolean
}

export const ABSENCE_KINDS: AbsenceMeta[] = [
  { kind: 'vacation', code: 'ОТ', label: 'Отпуск оплачиваемый', bg: '#dbeafe', color: '#1d4ed8', paid: true },
  { kind: 'sick', code: 'Б', label: 'Больничный', bg: '#fed7aa', color: '#c2410c', paid: true },
  { kind: 'unpaid', code: 'ДО', label: 'Отпуск за свой счёт', bg: '#e5e7eb', color: '#4b5563', paid: false },
  { kind: 'absent', code: 'Н', label: 'Неявка / прогул', bg: '#fecaca', color: '#b91c1c', paid: false },
]

const BY_KIND = new Map(ABSENCE_KINDS.map((a) => [a.kind, a]))

export function absenceMeta(kind: AbsenceKind | string): AbsenceMeta | undefined {
  return BY_KIND.get(kind as AbsenceKind)
}
