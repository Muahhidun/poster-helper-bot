import React from 'react'

interface ErrorMessageProps {
  message: string
  onRetry?: () => void
}

export const ErrorMessage: React.FC<ErrorMessageProps> = ({ message, onRetry }) => {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-4">
      <div className="text-red-500 text-5xl mb-4">⚠️</div>
      <h2 className="text-xl font-bold mb-2">Ошибка</h2>
      <p className="text-gray-600 text-center mb-4">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
        >
          Попробовать снова
        </button>
      )}
    </div>
  )
}
