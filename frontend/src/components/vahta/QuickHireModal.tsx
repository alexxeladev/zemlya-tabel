import { useEffect, useState } from 'react'

import { findSimilarEmployees, createVahtaAssignment, quickHireGuard } from '../../api/vahta'
import { Button } from '../Button'
import { Modal } from '../Modal'
import { toast } from '../../store/toasts'
import type { GuardJobTitle, VahtaSimilarEmployee } from '../../types/api'
import { defaultJobTitleId, useGuardJobTitles } from '../../hooks/useGuardJobTitles'

/** Место работы: пост объекта или выездной экипаж ГБР (см. VahtaPage). */
export interface HirePlace {
  key: string
  kind: 'post' | 'crew'
  id: number
  label: string
  rate: string | null
}

/**
 * «Оформить нового» — быстрый найм из ТРЁХ полей: ФИО, пост, ставка.
 *
 * Заводит ОБЫЧНОГО сотрудника общего справочника с позицией в отделе охраны:
 * отдельного справочника охранников нет, иначе один человек оказался бы в двух
 * местах. Табельный номер присваивается автоматически.
 *
 * «Пост» здесь — МЕСТО РАБОТЫ: пост внутри объекта либо выездной экипаж ГБР.
 *
 * При вводе ФИО ищутся похожие — срочный найм не должен плодить дубли. Нашёлся
 * похожий: предлагаем поставить ЕГО, а не создавать нового.
 */
export function QuickHireModal({
  year,
  month,
  places,
  allPlaces,
  onClose,
  onDone,
}: {
  year: number
  month: number
  /** Места этой карточки — они предлагаются первыми. */
  places: HirePlace[]
  /** Все места: человека иногда оформляют сразу на другой объект. */
  allPlaces: HirePlace[]
  onClose: () => void
  onDone: () => void
}) {
  const options = places.length > 0 ? places : allPlaces
  const [fullName, setFullName] = useState('')
  const [placeKey, setPlaceKey] = useState<string>(options[0]?.key ?? '')
  const [jobTitleId, setJobTitleId] = useState<number | ''>('')
  const jobTitles: GuardJobTitle[] = useGuardJobTitles()
  const [rate, setRate] = useState('')
  const [similar, setSimilar] = useState<VahtaSimilarEmployee[]>([])
  const [saving, setSaving] = useState(false)

  const place = allPlaces.find((p) => p.key === placeKey) ?? options.find((p) => p.key === placeKey)
  const chosenTitle = jobTitles.find(
    (t) => t.id === (jobTitleId || (place ? defaultJobTitleId(jobTitles, place.kind) : undefined)),
  )

  // Ставка подставляется от места и остаётся правимой (в образце у двух ГБР
  // одного экипажа ставки разные).
  useEffect(() => {
    if (place?.rate) setRate(String(parseFloat(place.rate)))
  }, [place?.key, place?.rate])

  // Проверка на дубли — по мере ввода, с паузой, чтобы не дёргать сервер на
  // каждую букву.
  useEffect(() => {
    if (fullName.trim().length < 3) {
      setSimilar([])
      return
    }
    const timer = window.setTimeout(() => {
      findSimilarEmployees(fullName.trim()).then(setSimilar).catch(() => setSimilar([]))
    }, 350)
    return () => window.clearTimeout(timer)
  }, [fullName])

  const placeRef = (p: HirePlace) =>
    p.kind === 'post' ? { post_id: p.id } : { crew_id: p.id }

  const hire = async () => {
    if (!place) return
    setSaving(true)
    try {
      const result = await quickHireGuard({
        full_name: fullName.trim(),
        ...placeRef(place),
        job_title_id: jobTitleId || defaultJobTitleId(jobTitles, place.kind) || null,
        rate: rate || null,
        year,
        month,
        assign: true,
      })
      toast.success(`Оформлен, табельный номер ${result.tab_number ?? '—'}`)
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось оформить')
    } finally {
      setSaving(false)
    }
  }

  const useExisting = async (employeeId: number) => {
    if (!place) return
    setSaving(true)
    try {
      await createVahtaAssignment({
        year,
        month,
        ...placeRef(place),
        job_title_id: jobTitleId || defaultJobTitleId(jobTitles, place.kind) || null,
        employee_id: employeeId,
      })
      toast.success('Поставлен на место')
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось поставить на пост')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title="Оформить нового"
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button
            onClick={hire}
            loading={saving}
            disabled={fullName.trim().length < 3 || !place}
          >
            Оформить и поставить
          </Button>
        </>
      }
    >
      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">ФИО</span>
        <input
          autoFocus
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          placeholder="Караулов Олег Петрович"
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        />
      </label>

      {similar.length > 0 && (
        <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 p-3">
          <p className="mb-2 text-xs font-medium text-amber-900">
            Похожие уже есть в справочнике. Возможно, нужен кто-то из них — тогда
            нового заводить не надо:
          </p>
          <div className="flex flex-col gap-1">
            {similar.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => void useExisting(s.id)}
                className="flex cursor-pointer items-baseline justify-between rounded px-2 py-1 text-left text-sm hover:bg-amber-100"
              >
                <span className="text-slate-800">
                  {s.full_name}
                  {!s.is_active && <span className="ml-2 text-xs text-gray-400">уволен</span>}
                </span>
                <span className="text-xs text-gray-500">
                  {s.tab_number ?? ''} {s.department_name ?? ''}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">Место работы</span>
        <select
          value={placeKey}
          onChange={(e) => setPlaceKey(e.target.value)}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        >
          {options.length === 0 && <option value="">Мест нет</option>}
          {options.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">Должность</span>
        <select
          value={jobTitleId || (place ? defaultJobTitleId(jobTitles, place.kind) : '') || ''}
          onChange={(e) => setJobTitleId(Number(e.target.value))}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        >
          {jobTitles.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm">
        <span className="mb-1 block text-gray-600">
          Ставка {chosenTitle?.pay_type === 'salary' ? 'за месяц' : 'за смену'}
        </span>
        <input
          value={rate}
          onChange={(e) => setRate(e.target.value)}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-right"
        />
      </label>

      <p className="mt-3 text-xs text-gray-500">
        Табельный номер присвоится автоматически. Кадровые поля дозаполняются
        позже в карточке сотрудника, если понадобятся.
      </p>
    </Modal>
  )
}
