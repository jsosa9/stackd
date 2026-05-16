'use client';

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const TWILIO_NUMBER = process.env.NEXT_PUBLIC_TWILIO_NUMBER ?? '';

type State = 'loading' | 'success' | 'error';

function UnsubscribeContent() {
  const searchParams = useSearchParams();
  const [state, setState] = useState<State>('loading');

  useEffect(() => {
    const token = searchParams.get('token');
    if (!token) { setState('error'); return; }

    fetch(`${API_URL}/api/unsubscribe?token=${encodeURIComponent(token)}`)
      .then(r => r.json())
      .then(data => setState(data.success ? 'success' : 'error'))
      .catch(() => setState('error'));
  }, [searchParams]);

  return (
    <main className="unsub-main">
      <div className="unsub-card">

        {state === 'loading' && (
          <>
            <div className="unsub-spinner" role="status" aria-label="Processing" />
            <p className="unsub-sub">One moment…</p>
          </>
        )}

        {state === 'success' && (
          <>
            <div className="unsub-icon" aria-hidden="true">✓</div>
            <h1 className="unsub-h1">You&apos;re unsubscribed.</h1>
            <p className="unsub-sub">No more messages will be sent to your number.</p>
            <p className="unsub-small">
              Changed your mind?{TWILIO_NUMBER ? (
                <> Text <strong>START</strong> to <a href={`sms:${TWILIO_NUMBER}`}>{TWILIO_NUMBER}</a></>
              ) : (
                <> Text <strong>START</strong> to your stackd number</>
              )}
            </p>
            <Link href="/" className="unsub-home">← Back to stackd.chat</Link>
          </>
        )}

        {state === 'error' && (
          <>
            <div className="unsub-icon" aria-hidden="true">⚠</div>
            <h1 className="unsub-h1">Link expired.</h1>
            <p className="unsub-sub">This link has already been used or has expired.</p>
            <p className="unsub-small">
              Email <a href="mailto:support@stackd.chat">support@stackd.chat</a> if you need help.
            </p>
            <Link href="/" className="unsub-home">← Back to stackd.chat</Link>
          </>
        )}

      </div>
    </main>
  );
}

export default function UnsubscribePage() {
  return (
    <>
      <style>{`
        .unsub-root {
          min-height: 100vh;
          background: #FAF7F2;
          color: #1A1612;
          font-family: var(--font-dm-sans), -apple-system, sans-serif;
          display: flex;
          flex-direction: column;
        }
        .unsub-nav {
          padding: 16px 28px;
          border-bottom: 1px solid rgba(26,22,18,0.08);
          background: #FAF7F2;
        }
        .unsub-wordmark {
          font-family: var(--font-dm-serif), serif;
          font-size: 22px;
          color: #1A1612;
          text-decoration: none;
          letter-spacing: -0.01em;
        }
        .unsub-main {
          flex: 1;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 64px 28px;
        }
        .unsub-card {
          max-width: 440px;
          width: 100%;
          text-align: center;
        }
        .unsub-icon {
          font-size: 48px;
          margin-bottom: 24px;
        }
        .unsub-h1 {
          font-family: var(--font-dm-serif), serif;
          font-size: clamp(28px, 6vw, 38px);
          line-height: 1.1;
          letter-spacing: -0.02em;
          color: #1A1612;
          margin: 0 0 14px;
        }
        .unsub-sub {
          font-size: 15px;
          line-height: 1.65;
          color: rgba(26,22,18,0.55);
          margin: 0 0 20px;
        }
        .unsub-small {
          font-size: 13px;
          color: rgba(26,22,18,0.38);
          line-height: 1.6;
          margin: 0 0 36px;
        }
        .unsub-small a {
          color: #C8A97A;
          text-decoration: none;
        }
        .unsub-small a:hover { text-decoration: underline; }
        .unsub-home {
          display: inline-block;
          font-size: 13px;
          color: rgba(26,22,18,0.45);
          text-decoration: none;
          border-bottom: 1px solid rgba(26,22,18,0.2);
          padding-bottom: 2px;
          transition: color 0.15s;
        }
        .unsub-home:hover { color: #1A1612; }
        .unsub-spinner {
          width: 28px;
          height: 28px;
          border: 2px solid rgba(26,22,18,0.1);
          border-top-color: #C8A97A;
          border-radius: 50%;
          animation: spin 0.7s linear infinite;
          margin: 0 auto 20px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      <div className="unsub-root">
        <nav className="unsub-nav">
          <Link href="/" className="unsub-wordmark">stackd</Link>
        </nav>

        <Suspense fallback={
          <main className="unsub-main">
            <div className="unsub-card">
              <div className="unsub-spinner" role="status" aria-label="Loading" />
            </div>
          </main>
        }>
          <UnsubscribeContent />
        </Suspense>
      </div>
    </>
  );
}
