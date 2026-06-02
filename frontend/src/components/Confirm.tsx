import { Modal } from './Modal'
import { Button } from './Button'

interface Props {
  isOpen: boolean
  onConfirm: () => void
  onCancel: () => void
  title?: string
  message: string
  danger?: boolean
}

export function Confirm({ isOpen, onConfirm, onCancel, title = 'Подтверждение', message, danger }: Props) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onCancel}
      title={title}
      actions={
        <>
          <Button type="button" variant="ghost" onClick={onCancel}>Отмена</Button>
          <Button
            type="button"
            variant={danger ? 'danger' : 'primary'}
            onClick={onConfirm}
          >
            Подтвердить
          </Button>
        </>
      }
    >
      <p className="text-sm text-gray-700">{message}</p>
    </Modal>
  )
}
