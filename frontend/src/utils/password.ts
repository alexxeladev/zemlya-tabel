/**
 * Политика паролей — ЗЕРКАЛО `password_policy_error` в
 * `backend/app/core/security.py` (task_stage2_access п.2.3). Решает сервер:
 * здесь та же проверка, чтобы причина была видна до отправки. Правишь одно —
 * правь второе (сверку держит `password.test.ts`).
 */
export const PASSWORD_MIN_LENGTH = 8
/** bcrypt берёт только первые 72 байта, хвост молча отбрасывает. */
export const PASSWORD_MAX_BYTES = 72

export function passwordPolicyError(plain: string | null | undefined): string | null {
  if (plain == null || plain === '') return 'Пароль не может быть пустым'
  if (!plain.trim()) return 'Пароль не может состоять из одних пробелов'
  if ([...plain].length < PASSWORD_MIN_LENGTH) {
    return `Пароль должен быть не короче ${PASSWORD_MIN_LENGTH} символов`
  }
  if (new TextEncoder().encode(plain).length > PASSWORD_MAX_BYTES) {
    return `Пароль слишком длинный: не больше ${PASSWORD_MAX_BYTES} байт (латиница — 72 символа, кириллица — 36)`
  }
  return null
}
