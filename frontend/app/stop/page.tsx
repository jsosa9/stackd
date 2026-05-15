import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Unsubscribe — stackd',
  description: 'Stop receiving SMS messages from stackd.',
};

export default function StopPage() {
  return (
    <>
      <style>{`
        .stop-root {
          min-height: 100vh;
          background: #FAF7F2;
          color: #1A1612;
          font-family: var(--font-dm-sans), -apple-system, sans-serif;
          display: flex;
          flex-direction: column;
        }
        .stop-nav {
          position: sticky;
          top: 0;
          z-index: 50;
          background: #FAF7F2;
          border-bottom: 1px solid rgba(26,22,18,0.08);
          padding: 16px 28px;
          display: flex;
          align-items: center;
          justify-content: space-between;
        }
        .stop-wordmark {
          font-family: var(--font-dm-serif), serif;
          font-size: 22px;
          color: #1A1612;
          text-decoration: none;
          letter-spacing: -0.01em;
        }
        .stop-back {
          font-size: 13px;
          color: rgba(26,22,18,0.45);
          text-decoration: none;
          transition: color 0.15s;
        }
        .stop-back:hover { color: #1A1612; }
        .stop-main {
          flex: 1;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 64px 28px;
        }
        .stop-card {
          max-width: 480px;
          width: 100%;
          text-align: center;
        }
        .stop-eyebrow {
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #C8A97A;
          margin-bottom: 14px;
        }
        .stop-h1 {
          font-family: var(--font-dm-serif), serif;
          font-size: clamp(28px, 6vw, 40px);
          line-height: 1.1;
          letter-spacing: -0.02em;
          color: #1A1612;
          margin: 0 0 20px;
        }
        .stop-desc {
          font-size: 15px;
          line-height: 1.7;
          color: rgba(26,22,18,0.55);
          margin-bottom: 40px;
        }
        .stop-options {
          display: flex;
          flex-direction: column;
          gap: 14px;
          margin-bottom: 48px;
        }
        .stop-option {
          background: #FFFFFF;
          border: 1px solid rgba(26,22,18,0.1);
          border-radius: 14px;
          padding: 20px 24px;
          text-align: left;
        }
        .stop-option-label {
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: #C8A97A;
          margin-bottom: 6px;
        }
        .stop-option-main {
          font-family: var(--font-dm-serif), serif;
          font-size: 20px;
          color: #1A1612;
          margin-bottom: 4px;
        }
        .stop-option-sub {
          font-size: 13px;
          color: rgba(26,22,18,0.45);
          line-height: 1.5;
        }
        .stop-option-link {
          color: #C8A97A;
          text-decoration: none;
        }
        .stop-option-link:hover { text-decoration: underline; }
        .stop-note {
          font-size: 13px;
          color: rgba(26,22,18,0.35);
          line-height: 1.6;
          margin-bottom: 32px;
        }
        .stop-home-link {
          display: inline-block;
          font-size: 14px;
          font-weight: 600;
          color: rgba(26,22,18,0.5);
          text-decoration: none;
          border-bottom: 1px solid rgba(26,22,18,0.2);
          padding-bottom: 2px;
          transition: color 0.15s, border-color 0.15s;
        }
        .stop-home-link:hover {
          color: #1A1612;
          border-color: rgba(26,22,18,0.5);
        }
      `}</style>

      <div className="stop-root">
        <nav className="stop-nav">
          <Link href="/" className="stop-wordmark">stackd</Link>
          <Link href="/" className="stop-back">← Back to home</Link>
        </nav>

        <main className="stop-main">
          <div className="stop-card">
            <p className="stop-eyebrow">Unsubscribe</p>
            <h1 className="stop-h1">Stop receiving messages.</h1>
            <p className="stop-desc">
              You can unsubscribe from stackd at any time using either option below.
            </p>

            <div className="stop-options">
              <div className="stop-option">
                <p className="stop-option-label">Option 1 — Instant</p>
                <p className="stop-option-main">Text STOP</p>
                <p className="stop-option-sub">
                  Reply <strong>STOP</strong> to any message from your stackd number.
                  You&apos;ll be removed immediately and receive a confirmation text.
                </p>
              </div>

              <div className="stop-option">
                <p className="stop-option-label">Option 2 — Email</p>
                <p className="stop-option-main">
                  <a href="mailto:support@stackd.chat?subject=Unsubscribe request" className="stop-option-link">
                    support@stackd.chat
                  </a>
                </p>
                <p className="stop-option-sub">
                  Email us with your phone number and we&apos;ll remove you within 24 hours.
                </p>
              </div>
            </div>

            <p className="stop-note">
              After unsubscribing you will receive no further messages from stackd.<br />
              You can resubscribe at any time by texting us again.
            </p>

            <Link href="/" className="stop-home-link">← Back to stackd.chat</Link>
          </div>
        </main>
      </div>
    </>
  );
}
