import React, { useEffect } from 'react'
import { useParams } from 'react-router-dom'

/**
 * Redirects to the existing Enketo redirect endpoint.
 * For anonymous users, the backend checks the submission flags
 * and sets a cookie before redirecting to Enketo.
 */
export default function LinkAccessRedirect({ action }: { action: 'edit' | 'view' }) {
  const { uid, submissionId } = useParams<{ uid: string; submissionId: string }>()

  useEffect(() => {
    if (uid && submissionId) {
      window.location.href = `/api/v2/assets/${uid}/data/${submissionId}/enketo/redirect/${action}/`
    }
  }, [uid, submissionId, action])

  return <div style={{ padding: '2rem', textAlign: 'center' }}>Redirigint a Enketo…</div>
}
