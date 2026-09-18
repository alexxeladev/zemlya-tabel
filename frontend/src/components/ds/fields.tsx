import { useEffect, useId, useRef, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'

import { isoToRu, ruToIso } from '../../utils/ruDate'
import { Icon } from './Icon'

/**
 * Поля ввода дизайн-системы (`.fin`, `.search`, `.selectwrap` макетов).
 *
 * Рамка поля — `control-line` (3:1 к фону), а не `line-strong` макета (1,56:1):
 * граница элемента управления обязана быть различима (WCAG 1.4.11).
 */

const CONTROL =
  'h-8 w-full rounded-ds-md border border-ds-control-line bg-ds-surface px-2.5 text-[13px] text-ds-ink transition-shadow placeholder:text-ds-muted focus:border-ds-accent focus:shadow-[0_0_0_3px_color-mix(in_srgb,var(--ds-accent)_16%,transparent)] focus:outline-none disabled:bg-ds-surface-3 disabled:text-ds-muted'

/** Строка формы: подпись слева, поле справа, подсказка под полем. */
export function FieldRow({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string
  hint?: ReactNode
  children: ReactNode
  htmlFor?: string
}) {
  return (
    <div className="mb-2.5 grid grid-cols-[140px_minmax(0,1fr)] items-center gap-x-3 gap-y-1">
      <label htmlFor={htmlFor} className="text-[12.5px] text-ds-muted">
        {label}
      </label>
      <div className="min-w-0">{children}</div>
      {hint && <p className="col-start-2 m-0 text-[11.5px] text-ds-muted">{hint}</p>}
    </div>
  )
}

export function TextField({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${CONTROL} ${className}`} />
}

export function SelectField({
  className = '',
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={`relative inline-block ${className}`}>
      <select {...props} className={`${CONTROL} cursor-pointer appearance-none pr-8`}>
        {children}
      </select>
      <Icon
        name="chevronDown"
        size={12}
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-ds-faint"
      />
    </span>
  )
}

export function SearchField({
  className = '',
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { 'aria-label': string }) {
  return (
    <span className={`relative inline-block ${className}`}>
      <Icon
        name="search"
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ds-faint"
      />
      <input type="search" {...props} className={`${CONTROL} pl-8`} />
    </span>
  )
}

// ── Дата по-русски ────────────────────────────────────────────

/**
 * Поле даты в формате ДД.ММ.ГГГГ.
 *
 * Вместо `<input type="date">`: тот рисует `mm/dd/yyyy` по локали браузера под
 * русской подписью (§4.3). Значение наружу — ISO или `''`; пока текст не
 * распознан, наружу ничего не уходит, а поле подсвечено ошибкой.
 */
export function DateField({
  value,
  onChange,
  id,
  placeholder = 'ДД.ММ.ГГГГ',
  ...rest
}: {
  value: string
  onChange: (iso: string) => void
  id?: string
  placeholder?: string
  'aria-label'?: string
  'aria-describedby'?: string
}) {
  const [text, setText] = useState(isoToRu(value))
  const [invalid, setInvalid] = useState(false)
  const errorId = useId()
  // Что поле отдало наружу последним. Пока человек набирает «14.08.20», это
  // уже законная дата (2020 год), и переписать текст значением снаружи значило
  // бы оборвать ввод на полуслове. Снаружи синхронизируем только ЧУЖОЕ значение.
  const emitted = useRef(value)

  useEffect(() => {
    if (value === emitted.current) return
    emitted.current = value
    setText(isoToRu(value))
    setInvalid(false)
  }, [value])

  return (
    <>
      <input
        id={id}
        inputMode="numeric"
        value={text}
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? errorId : rest['aria-describedby']}
        aria-label={rest['aria-label']}
        onChange={(e) => {
          setText(e.target.value)
          const iso = ruToIso(e.target.value)
          if (iso !== null) {
            setInvalid(false)
            emitted.current = iso
            onChange(iso)
          }
        }}
        onBlur={() => setInvalid(ruToIso(text) === null)}
        className={`${CONTROL} max-w-[140px] tabular-nums ${invalid ? 'border-ds-danger' : ''}`}
      />
      {invalid && (
        <p id={errorId} className="m-0 mt-1 text-[11.5px] text-ds-danger">
          Дата в формате ДД.ММ.ГГГГ, например 01.08.2026
        </p>
      )}
    </>
  )
}
