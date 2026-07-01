import { create } from 'zustand'

export type ToastVariant = 'success' | 'error' | 'info'

export interface Toast {
  id: string
  message: string
  variant: ToastVariant
}

interface ToastState {
  toasts: Toast[]
  add: (message: string, variant?: ToastVariant) => void
  remove: (id: string) => void
}

// crypto.randomUUID() есть только в secure context (HTTPS/localhost). На препроде
// по http://<IP>:порт его нет → бросает и ломает всю цепочку успеха (модалка не
// закрывается). Фолбэк на случай небезопасного origin.
function uid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  add: (message, variant = 'info') => {
    const id = uid()
    set((s) => ({ toasts: [...s.toasts, { id, message, variant }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000)
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

export const toast = {
  success: (msg: string) => useToastStore.getState().add(msg, 'success'),
  error: (msg: string) => useToastStore.getState().add(msg, 'error'),
  info: (msg: string) => useToastStore.getState().add(msg, 'info'),
}
