import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Help — stackd',
  description: 'SMS commands, unsubscribe instructions, and support for stackd.',
};

export default function HelpPage() {
  return (
    <>
      <style>{`
        .help-root {
          min-height: 100vh;
          background: #FAF7F2;
          color: #1A1612;
          font-family: var(--font-dm-sans), -apple-system, sans-serif;
        }
        .help-nav {
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
        .help-wordmark {
          font-family: var(--font-dm-serif), serif;
          font-size: 22px;
          color: #1A1612;
          text-decoration: none;
          letter-spacing: -0.01em;
        }
        .help-back {
          font-size: 13px;
          color: rgba(26,22,18,0.45);
          text-decoration: none;
          transition: color 0.15s;
        }
        .help-back:hover { color: #1A1612; }
        .help-main {
          max-width: 680px;
          margin: 0 auto;
          padding: 56px 28px 96px;
        }
        .help-eyebrow {
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #C8A97A;
          margin-bottom: 14px;
        }
        .help-h1 {
          font-family: var(--font-dm-serif), serif;
          font-size: clamp(32px, 6vw, 46px);
          line-height: 1.08;
          letter-spacing: -0.02em;
          color: #1A1612;
          margin: 0 0 52px;
        }
        .help-section {
          margin-bottom: 48px;
        }
        .help-h2 {
          font-family: var(--font-dm-serif), serif;
          font-size: 20px;
          letter-spacing: -0.01em;
          color: #1A1612;
          margin: 0 0 18px;
        }
        .help-divider {
          height: 1px;
          background: rgba(26,22,18,0.07);
          margin: 48px 0;
        }
        .help-p {
          font-size: 15px;
          line-height: 1.75;
          color: rgba(26,22,18,0.62);
          margin: 0 0 16px;
        }

        /* SMS commands table */
        .help-cmd-table {
          width: 100%;
          border-collapse: collapse;
        }
        .help-cmd-table tr {
          border-bottom: 1px solid rgba(26,22,18,0.07);
        }
        .help-cmd-table tr:last-child {
          border-bottom: none;
        }
        .help-cmd-table td {
          padding: 14px 0;
          font-size: 15px;
          line-height: 1.5;
          vertical-align: top;
        }
        .help-cmd-table td:first-child {
          font-family: var(--font-dm-serif), serif;
          font-size: 16px;
          color: #1A1612;
          width: 90px;
          padding-right: 20px;
        }
        .help-cmd-table td:last-child {
          color: rgba(26,22,18,0.55);
        }

        /* Links */
        .help-link {
          color: #C8A97A;
          text-decoration: none;
          font-size: 15px;
        }
        .help-link:hover { text-decoration: underline; }

        .help-policy-links {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        /* Footer */
        .help-footer {
          border-top: 1px solid rgba(26,22,18,0.08);
          padding: 24px 28px;
          display: flex;
          flex-direction: column;
          gap: 12px;
          background: #FAF7F2;
        }
        .help-footer-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-wrap: wrap;
          gap: 10px;
        }
        .help-footer-logo {
          font-family: var(--font-dm-serif), serif;
          font-size: 18px;
          color: #1A1612;
        }
        .help-footer-copy {
          font-size: 12px;
          color: rgba(26,22,18,0.28);
        }
        .help-footer-links {
          display: flex;
          gap: 20px;
          flex-wrap: wrap;
        }
        .help-footer-links a {
          font-size: 12px;
          color: rgba(26,22,18,0.35);
          text-decoration: none;
        }
        .help-footer-links a:hover { color: #1A1612; }
      `}</style>

      <div className="help-root">
        <nav className="help-nav">
          <Link href="/" className="help-wordmark">stackd</Link>
          <Link href="/" className="help-back">← Back to home</Link>
        </nav>

        <main className="help-main">
          <p className="help-eyebrow">Help</p>
          <h1 className="help-h1">We&apos;ve got you.</h1>

          {/* Section 1 — SMS Commands */}
          <div className="help-section">
            <h2 className="help-h2">Text commands</h2>
            <table className="help-cmd-table">
              <tbody>
                <tr>
                  <td>STOP</td>
                  <td>Unsubscribe from all messages</td>
                </tr>
                <tr>
                  <td>START</td>
                  <td>Resubscribe after stopping</td>
                </tr>
                <tr>
                  <td>HELP</td>
                  <td>Get a link to this help page</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="help-divider" />

          {/* Section 2 — Legal */}
          <div className="help-section">
            <h2 className="help-h2">Policies</h2>
            <div className="help-policy-links">
              <Link href="/privacy" className="help-link">Privacy Policy →</Link>
              <Link href="/terms" className="help-link">Terms of Service →</Link>
            </div>
          </div>

          <div className="help-divider" />

          {/* Section 4 — Contact */}
          <div className="help-section">
            <h2 className="help-h2">Still need help?</h2>
            <p className="help-p">
              Email us at{' '}
              <a href="mailto:support@stackd.chat" className="help-link">support@stackd.chat</a>
              {' '}and we will get back to you within 24 hours.
            </p>
          </div>
        </main>

        <footer className="help-footer">
          <div className="help-footer-row">
            <span className="help-footer-logo">stackd</span>
            <span className="help-footer-copy">© 2026 stackd · 7 days free · $9.99/mo after</span>
          </div>
          <div className="help-footer-links">
            <Link href="/privacy">Privacy Policy</Link>
            <Link href="/terms">Terms of Service</Link>
            <Link href="/unsubscribe">Unsubscribe</Link>
            <Link href="/help">Help</Link>
            <a href="mailto:support@stackd.chat">Contact</a>
          </div>
        </footer>
      </div>
    </>
  );
}
