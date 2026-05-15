import Link from 'next/link';
import type { Metadata } from 'next';
import LegalTOC from '@/components/LegalTOC';

export const metadata: Metadata = {
  title: 'Terms of Service — stackd',
  description: 'Terms and conditions for using the stackd SMS coaching service.',
};

const SECTIONS = [
  { id: 'section-1',  label: '1. The Service' },
  { id: 'section-2',  label: '2. Eligibility' },
  { id: 'section-3',  label: '3. SMS Opt-In and Opt-Out' },
  { id: 'section-4',  label: '4. Message Frequency and Rates' },
  { id: 'section-5',  label: '5. Free Trial and Billing' },
  { id: 'section-6',  label: '6. AI and Public Figures' },
  { id: 'section-7',  label: '7. Prohibited Use' },
  { id: 'section-8',  label: '8. Disclaimers' },
  { id: 'section-9',  label: '9. Limitation of Liability' },
  { id: 'section-10', label: '10. Changes to Terms' },
  { id: 'section-11', label: '11. Contact' },
];

export default function TermsPage() {
  return (
    <>
      <style>{`
        .legal-root {
          min-height: 100vh;
          background: #FAF7F2;
          color: #1A1612;
          font-family: var(--font-dm-sans), -apple-system, sans-serif;
        }
        .legal-nav {
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
        .legal-wordmark {
          font-family: var(--font-dm-serif), serif;
          font-size: 22px;
          color: #1A1612;
          text-decoration: none;
          letter-spacing: -0.01em;
        }
        .legal-back {
          font-size: 13px;
          color: rgba(26,22,18,0.45);
          text-decoration: none;
          transition: color 0.15s;
        }
        .legal-back:hover { color: #1A1612; }

        /* Two-column wrapper */
        .legal-outer {
          max-width: 960px;
          margin: 0 auto;
          padding: 56px 28px 96px;
          display: flex;
          gap: 56px;
          align-items: flex-start;
        }
        @media (max-width: 720px) {
          .legal-outer {
            flex-direction: column;
            gap: 0;
            padding-top: 40px;
          }
        }

        /* Content column */
        .legal-content {
          flex: 1;
          min-width: 0;
        }
        .legal-eyebrow {
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #C8A97A;
          margin-bottom: 14px;
        }
        .legal-h1 {
          font-family: var(--font-dm-serif), serif;
          font-size: clamp(32px, 6vw, 46px);
          line-height: 1.08;
          letter-spacing: -0.02em;
          color: #1A1612;
          margin: 0 0 16px;
        }
        .legal-effective {
          font-size: 13px;
          color: rgba(26,22,18,0.4);
          margin-bottom: 52px;
        }
        .legal-section {
          margin-bottom: 44px;
          scroll-margin-top: 88px;
        }
        .legal-h2 {
          font-family: var(--font-dm-serif), serif;
          font-size: 20px;
          letter-spacing: -0.01em;
          color: #1A1612;
          margin: 0 0 14px;
        }
        .legal-p {
          font-size: 15px;
          line-height: 1.75;
          color: rgba(26,22,18,0.72);
          margin: 0 0 12px;
        }
        .legal-ul {
          list-style: none;
          padding: 0;
          margin: 0 0 12px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .legal-ul li {
          font-size: 15px;
          line-height: 1.65;
          color: rgba(26,22,18,0.72);
          padding-left: 18px;
          position: relative;
        }
        .legal-ul li::before {
          content: '';
          position: absolute;
          left: 0;
          top: 10px;
          width: 5px;
          height: 5px;
          border-radius: 50%;
          background: #C8A97A;
        }
        .legal-divider {
          height: 1px;
          background: rgba(26,22,18,0.07);
          margin: 44px 0;
        }
        .legal-footer {
          border-top: 1px solid rgba(26,22,18,0.08);
          padding: 24px 28px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-wrap: wrap;
          gap: 10px;
          background: #FAF7F2;
        }
        .legal-footer-logo {
          font-family: var(--font-dm-serif), serif;
          font-size: 18px;
          color: #1A1612;
        }
        .legal-footer-copy {
          font-size: 12px;
          color: rgba(26,22,18,0.28);
        }
        .legal-link {
          color: #C8A97A;
          text-decoration: none;
        }
        .legal-link:hover { text-decoration: underline; }
      `}</style>

      <div className="legal-root">
        <nav className="legal-nav">
          <Link href="/" className="legal-wordmark">stackd</Link>
          <Link href="/" className="legal-back">← Back to home</Link>
        </nav>

        <div className="legal-outer">
          <LegalTOC sections={SECTIONS} />

          <main className="legal-content">
            <p className="legal-eyebrow">Terms of Service</p>
            <h1 className="legal-h1">The rules of the game.</h1>
            <p className="legal-effective">Effective date: June 1, 2026 · Contact: <a href="mailto:support@stackd.chat" className="legal-link">support@stackd.chat</a></p>

            <div id="section-1" className="legal-section">
              <h2 className="legal-h2">1. The Service</h2>
              <ul className="legal-ul">
                <li>stackd is an SMS-based AI coaching service</li>
                <li>You receive daily text messages from an AI that mimics the voice of a chosen public figure</li>
                <li>This is entertainment and accountability software</li>
                <li>It is not professional advice of any kind</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-2" className="legal-section">
              <h2 className="legal-h2">2. Eligibility</h2>
              <ul className="legal-ul">
                <li>You must be 18 or older to use this service</li>
                <li>You must have a valid US phone number</li>
                <li>You must provide accurate information</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-3" className="legal-section">
              <h2 className="legal-h2">3. SMS Opt-In and Opt-Out</h2>
              <ul className="legal-ul">
                <li>You opt in by texting our number first</li>
                <li>You can opt out at any time by texting <strong>STOP</strong></li>
                <li>After texting STOP you will receive no further messages from us</li>
                <li>Text <strong>HELP</strong> for customer support</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-4" className="legal-section">
              <h2 className="legal-h2">4. Message Frequency and Rates</h2>
              <ul className="legal-ul">
                <li>Message frequency varies based on your goals and settings — typically 1 to 5 per day</li>
                <li>Standard message and data rates may apply</li>
                <li>stackd is not responsible for carrier charges</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-5" className="legal-section">
              <h2 className="legal-h2">5. Free Trial and Billing</h2>
              <ul className="legal-ul">
                <li>New users receive a 7-day free trial</li>
                <li>No credit card is required to start</li>
                <li>After the trial period the service is $9.99 per month</li>
                <li>You will be notified before your trial ends</li>
                <li>Cancel any time by texting STOP or emailing <a href="mailto:support@stackd.chat" className="legal-link">support@stackd.chat</a></li>
                <li>Refunds are handled on a case by case basis — contact <a href="mailto:support@stackd.chat" className="legal-link">support@stackd.chat</a></li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-6" className="legal-section">
              <h2 className="legal-h2">6. AI and Public Figures</h2>
              <ul className="legal-ul">
                <li>Coaches are AI personas inspired by public figures</li>
                <li>stackd is not affiliated with any of the public figures mentioned</li>
                <li>Coach responses are AI-generated and do not represent the actual views or words of any real person</li>
                <li>Do not rely on coach responses for medical, legal, financial, or mental health decisions</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-7" className="legal-section">
              <h2 className="legal-h2">7. Prohibited Use</h2>
              <ul className="legal-ul">
                <li>Do not use this service if you are under 18</li>
                <li>Do not attempt to manipulate or jailbreak the AI</li>
                <li>Do not use the service for any illegal purpose</li>
                <li>We reserve the right to terminate accounts that violate these terms</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-8" className="legal-section">
              <h2 className="legal-h2">8. Disclaimers</h2>
              <ul className="legal-ul">
                <li>stackd is provided as-is without warranties</li>
                <li>We are not responsible for missed goals, missed messages, or technical failures</li>
                <li>This service is not a substitute for professional coaching, therapy, or medical advice</li>
                <li>If you are in crisis please contact emergency services or a mental health professional</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-9" className="legal-section">
              <h2 className="legal-h2">9. Limitation of Liability</h2>
              <ul className="legal-ul">
                <li>stackd&apos;s liability is limited to the amount you paid in the last 30 days</li>
                <li>We are not liable for indirect or consequential damages</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-10" className="legal-section">
              <h2 className="legal-h2">10. Changes to Terms</h2>
              <ul className="legal-ul">
                <li>We may update these terms at any time</li>
                <li>Continued use constitutes acceptance of changes</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-11" className="legal-section">
              <h2 className="legal-h2">11. Contact</h2>
              <ul className="legal-ul">
                <li>Email: <a href="mailto:support@stackd.chat" className="legal-link">support@stackd.chat</a></li>
                <li>Website: <a href="https://stackd.chat" className="legal-link">stackd.chat</a></li>
              </ul>
            </div>
          </main>
        </div>

        <footer className="legal-footer">
          <span className="legal-footer-logo">stackd</span>
          <span className="legal-footer-copy">© 2026 stackd · <Link href="/privacy" style={{ color: 'inherit', textDecoration: 'none' }}>Privacy</Link> · <Link href="/stop" style={{ color: 'inherit', textDecoration: 'none' }}>Unsubscribe</Link></span>
        </footer>
      </div>
    </>
  );
}
