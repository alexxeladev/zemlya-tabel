/**
 * Зеркало серверной политики паролей (task_stage2_access п.2.3).
 * Запуск: cd frontend && npm test
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { PASSWORD_MAX_BYTES, PASSWORD_MIN_LENGTH, passwordPolicyError } from './password.ts'

const here = dirname(fileURLToPath(import.meta.url))
const backend = readFileSync(resolve(here, '../../../backend/app/core/security.py'), 'utf8')

test('константы совпадают с бэком', () => {
  assert.match(backend, new RegExp(`PASSWORD_MIN_LENGTH = ${PASSWORD_MIN_LENGTH}\\b`))
  assert.match(backend, new RegExp(`PASSWORD_MAX_BYTES = ${PASSWORD_MAX_BYTES}\\b`))
})

test('негодные пароли отклоняются', () => {
  for (const bad of ['', '        ', 'short12', 'a'.repeat(73), 'ж'.repeat(37)]) {
    assert.notEqual(passwordPolicyError(bad), null, JSON.stringify(bad))
  }
})

test('граница 72 байта и 8 символов проходит', () => {
  assert.equal(passwordPolicyError('a'.repeat(72)), null)
  assert.equal(passwordPolicyError('ж'.repeat(36)), null)
  assert.equal(passwordPolicyError('abcd1234'), null)
})

test('тексты причин те же, что на бэке', () => {
  for (const msg of ['Пароль не может быть пустым', 'Пароль не может состоять из одних пробелов']) {
    assert.ok(backend.includes(msg), msg)
  }
})
