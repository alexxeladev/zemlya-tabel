import { useRef, useState } from 'react'
import { downloadImportTemplate, importEmployees } from '../../api/employees'
import { ApiError } from '../../api/client'
import { toast } from '../../store/toasts'
import type { EmployeeImportResult } from '../../types/api'
import { Button } from '../../components/Button'
import { formatMoney } from '../../utils/money'

interface Props {
  isOpen: boolean
  onClose: () => void
  /** Дёргается после успешного импорта — обновить список сотрудников */
  onImported: () => void
}

/**
 * Импорт сотрудников из Excel в три шага: скачать шаблон → загрузить файл →
 * посмотреть превью со статусами строк и подтвердить.
 *
 * Создаётся только то, что пользователь увидел валидным: на подтверждение
 * уходит тот же файл, бэк разбирает и валидирует его заново.
 */
export function EmployeeImportModal({ isOpen, onClose, onImported }: Props) {
  const fileInput = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<EmployeeImportResult | null>(null)
  const [parsing, setParsing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)

  const done = preview?.confirmed === true

  const reset = () => {
    setFile(null)
    setPreview(null)
    setFileError(null)
    setParsing(false)
    setImporting(false)
    if (fileInput.current) fileInput.current.value = ''
  }

  const close = () => {
    reset()
    onClose()
  }

  const onTemplate = async () => {
    try {
      const blob = await downloadImportTemplate()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'shablon_sotrudnikov.xlsx'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Не удалось скачать шаблон')
    }
  }

  const onPick = async (picked: File | null) => {
    setFile(picked)
    setPreview(null)
    setFileError(null)
    if (!picked) return
    try {
      setParsing(true)
      setPreview(await importEmployees(picked, false))
    } catch (e) {
      setFileError(e instanceof ApiError ? e.message : 'Не удалось разобрать файл')
    } finally {
      setParsing(false)
    }
  }

  const onConfirm = async () => {
    if (!file) return
    try {
      setImporting(true)
      const result = await importEmployees(file, true)
      setPreview(result)
      toast.success(`Создано сотрудников: ${result.created_count}`)
      onImported()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка импорта')
    } finally {
      setImporting(false)
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={close} />
      <div className="relative flex max-h-[90vh] w-full max-w-6xl flex-col rounded-xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">Импорт сотрудников из Excel</h2>
          <button
            type="button"
            onClick={close}
            className="text-xl leading-none text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* Шаг 1-2: шаблон и выбор файла */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Button type="button" variant="secondary" onClick={onTemplate}>
              Скачать шаблон Excel
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept=".xlsx"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              disabled={parsing || importing}
              className="text-sm text-gray-600 file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-gray-800 hover:file:bg-gray-200"
            />
            {parsing && <span className="text-sm text-gray-500">Разбираем файл…</span>}
          </div>

          {!preview && !fileError && !parsing && (
            <p className="text-sm text-gray-500">
              Заполните шаблон и выберите файл — перед импортом покажем, что распозналось.
              Строку «ПРИМЕР» удалять не обязательно, она не импортируется. Доступы
              (email, роль, пароль) через импорт не заводятся.
            </p>
          )}

          {fileError && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
              {fileError}
            </div>
          )}

          {/* Шаг 3: сводка + таблица превью */}
          {preview && (
            <>
              <div
                className={`mb-3 rounded-lg border p-4 text-sm ${
                  done
                    ? 'border-green-200 bg-green-50 text-green-800'
                    : 'border-blue-200 bg-blue-50 text-blue-900'
                }`}
              >
                {done ? (
                  <>
                    <span className="font-semibold">
                      Создано сотрудников: {preview.created_count}.
                    </span>{' '}
                    Пропущено: {preview.skipped_count} (ошибки).
                  </>
                ) : (
                  <>
                    Строк в файле: <b>{preview.total}</b> · Готово к импорту:{' '}
                    <b className="text-green-700">{preview.valid_count}</b> · С ошибками:{' '}
                    <b className={preview.error_count ? 'text-red-700' : ''}>
                      {preview.error_count}
                    </b>
                    {preview.error_count > 0 && (
                      <span className="ml-1 text-blue-800">
                        — строки с ошибками не импортируются, их можно поправить в файле и
                        загрузить снова.
                      </span>
                    )}
                  </>
                )}
              </div>

              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
                    <tr>
                      <th className="px-3 py-2">Стр.</th>
                      <th className="px-3 py-2">Статус</th>
                      <th className="px-3 py-2">Таб. №</th>
                      <th className="px-3 py-2">ФИО</th>
                      <th className="px-3 py-2">Компания</th>
                      <th className="px-3 py-2">Отдел</th>
                      <th className="px-3 py-2">Должность</th>
                      <th className="px-3 py-2">График</th>
                      <th className="px-3 py-2">Оплата</th>
                      <th className="px-3 py-2">Приём</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row) => (
                      <tr
                        key={row.row_number}
                        className={`border-t border-gray-100 align-top ${
                          row.is_valid ? '' : 'bg-red-50'
                        }`}
                      >
                        <td className="px-3 py-2 font-mono text-xs text-gray-500">
                          {row.row_number}
                        </td>
                        <td className="px-3 py-2">
                          {row.is_valid ? (
                            <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                              {done ? 'Создан' : 'Валидна'}
                            </span>
                          ) : (
                            <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                              {done ? 'Пропущена' : 'Ошибка'}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">{row.tab_number ?? '—'}</td>
                        <td className="px-3 py-2 font-medium text-gray-900">
                          {row.full_name ?? <span className="text-gray-400">—</span>}
                          {!row.is_valid && (
                            <ul className="mt-1 list-disc pl-4 text-xs font-normal text-red-700">
                              {row.errors.map((err) => (
                                <li key={err}>{err}</li>
                              ))}
                            </ul>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <Recognized value={row.company_name} raw={row.raw.company} />
                        </td>
                        <td className="px-3 py-2">
                          <Recognized value={row.department_name} raw={row.raw.department} />
                        </td>
                        <td className="px-3 py-2 text-gray-700">{row.position ?? '—'}</td>
                        <td className="px-3 py-2">
                          <Recognized value={row.schedule_name} raw={row.raw.schedule} />
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap text-gray-700">
                          {row.pay_type === 'per_shift'
                            ? `${formatMoney(row.shift_rate)} / смена`
                            : formatMoney(row.rate)}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap text-gray-700">
                          {row.hire_date ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-6 py-4">
          {done ? (
            <Button type="button" onClick={close}>
              Закрыть
            </Button>
          ) : (
            <>
              <Button type="button" variant="ghost" onClick={close}>
                Отмена
              </Button>
              <Button
                type="button"
                onClick={onConfirm}
                loading={importing}
                disabled={!preview || preview.valid_count === 0}
              >
                {preview
                  ? `Импортировать ${preview.valid_count} валидных`
                  : 'Импортировать'}
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** Распознанное значение справочника; если не распозналось — что было в файле. */
function Recognized({ value, raw }: { value: string | null; raw?: string }) {
  if (value) return <span className="text-gray-900">{value}</span>
  if (raw) return <span className="text-red-700 line-through">{raw}</span>
  return <span className="text-gray-400">—</span>
}
