import React, {useEffect, useState} from 'react';
import {useParams} from 'react-router-dom';

const fullPageStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: '#fff',
  zIndex: 9999,
};

/**
 * Two-step anonymous link-access flow (renders outside the App shell):
 * 1. Call the link-access auth endpoint (sets JWT cookie, returns numeric _id)
 * 2. Call the enketo API endpoint (returns Enketo URL)
 * 3. Redirect to Enketo
 *
 * Shows a blank white page while working — Enketo has its own loader.
 */
export default function LinkAccessRedirect({action}: {action: 'edit' | 'view'}) {
  const {uid, submissionId: rootUuid} = useParams<{uid: string; submissionId: string}>();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!uid || !rootUuid) {
      return;
    }

    const authenticate = async () => {
      try {
        // Step 1: Authenticate and get numeric submission ID
        const authResponse = await fetch(
          `/api/v2/assets/${uid}/data/${rootUuid}/link-access/${action}/`,
          {credentials: 'same-origin'}
        );
        if (!authResponse.ok) {
          const data = await authResponse.json().catch(() => null);
          setError(data?.detail || `Error d'autenticació (${authResponse.status})`);
          return;
        }
        const {submission_id: numericId} = await authResponse.json();

        // Step 2: Call Enketo API to get the edit/view URL
        const enketoAction = action === 'edit' ? 'enketo/edit' : 'enketo/view';
        const enketoResponse = await fetch(
          `/api/v2/assets/${uid}/data/${numericId}/${enketoAction}/`,
          {credentials: 'same-origin'}
        );
        if (!enketoResponse.ok) {
          const data = await enketoResponse.json().catch(() => null);
          setError(data?.detail || `Error d'Enketo (${enketoResponse.status})`);
          return;
        }
        const {url: enketoUrl} = await enketoResponse.json();

        // Step 3: Redirect to Enketo
        window.location.href = enketoUrl;
      } catch (err) {
        setError(String(err));
      }
    };

    authenticate();
  }, [uid, rootUuid, action]);

  return (
    <div style={fullPageStyle}>
      {error && <div style={{color: '#c00', fontSize: 14}}>{error}</div>}
    </div>
  );
}
