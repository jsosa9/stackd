import Link from 'next/link';
import type { Metadata } from 'next';
import LegalTOC from '@/components/LegalTOC';

export const metadata: Metadata = {
  title: 'Privacy Policy — stackd',
  description: 'How stackd collects, uses, and protects your personal information.',
};

const SECTIONS = [
  { id: 'section-1',  label: '1. Information We Collect' },
  { id: 'section-2',  label: '2. How We Use Your Information' },
  { id: 'section-3',  label: '3. SMS Messaging' },
  { id: 'section-4',  label: '4. Data Sharing' },
  { id: 'section-5',  label: '5. Data Retention' },
  { id: 'section-6',  label: '6. Your Rights' },
  { id: 'section-7',  label: '7. AI-Generated Content' },
  { id: 'section-8',  label: '8. Children' },
  { id: 'section-9',  label: '9. Changes to This Policy' },
  { id: 'section-10', label: '10. Contact' },
];

export default function PrivacyPage() {
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
            <p className="legal-eyebrow">Privacy Policy</p>
            <h1 className="legal-h1">How we handle your data.</h1>
            <p className="legal-effective">Effective date: June 1, 2026 · Contact: <a href="mailto:privacy@stackd.chat" className="legal-link">privacy@stackd.chat</a></p>

            <div id="section-1" className="legal-section">
              <h2 className="legal-h2">1. Information We Collect</h2>
              <ul className="legal-ul">
                <li>Phone number (when you text our number)</li>
                <li>Name and age, if provided during onboarding</li>
                <li>Goals and habits you share over SMS</li>
                <li>Messages sent and received through the service</li>
                <li>Food and nutrition information you provide</li>
                <li>Usage data and message timestamps</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-2" className="legal-section">
              <h2 className="legal-h2">2. How We Use Your Information</h2>
              <ul className="legal-ul">
                <li>To deliver daily SMS coaching messages</li>
                <li>To track your goals, streaks, and progress</li>
                <li>To personalize your coach&apos;s responses</li>
                <li>To send reminders you request</li>
                <li>To improve the service</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-3" className="legal-section">
              <h2 className="legal-h2">3. SMS Messaging</h2>
              <ul className="legal-ul">
                <li>You opt in by texting our number first</li>
                <li>Message frequency varies — typically 1 to 5 messages per day depending on your goals</li>
                <li>Standard message and data rates may apply</li>
                <li>Text <strong>STOP</strong> at any time to unsubscribe immediately</li>
                <li>Text <strong>HELP</strong> for support</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-4" className="legal-section">
              <h2 className="legal-h2">4. Data Sharing</h2>
              <ul className="legal-ul">
                <li>We do not sell your personal information</li>
                <li>We do not share your data with third parties for marketing purposes</li>
                <li>We use Supabase for secure data storage</li>
                <li>We use Twilio to deliver SMS messages</li>
                <li>We use Google Gemini AI to generate responses</li>
                <li>These service providers are bound by their own privacy policies</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-5" className="legal-section">
              <h2 className="legal-h2">5. Data Retention</h2>
              <ul className="legal-ul">
                <li>We retain your messages and goals for as long as your account is active</li>
                <li>You can request deletion of your data at any time by emailing <a href="mailto:privacy@stackd.chat" className="legal-link">privacy@stackd.chat</a></li>
                <li>We will delete your data within 30 days of your request</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-6" className="legal-section">
              <h2 className="legal-h2">6. Your Rights</h2>
              <ul className="legal-ul">
                <li>Access your data by contacting us</li>
                <li>Delete your data at any time</li>
                <li>Opt out of SMS by texting STOP</li>
                <li>Contact us at <a href="mailto:privacy@stackd.chat" className="legal-link">privacy@stackd.chat</a></li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-7" className="legal-section">
              <h2 className="legal-h2">7. AI-Generated Content</h2>
              <ul className="legal-ul">
                <li>Messages are generated by artificial intelligence</li>
                <li>Your coach is not a real person</li>
                <li>stackd is not a substitute for professional medical, mental health, or fitness advice</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-8" className="legal-section">
              <h2 className="legal-h2">8. Children</h2>
              <ul className="legal-ul">
                <li>This service is intended for users 18 and older</li>
                <li>We do not knowingly collect data from minors</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-9" className="legal-section">
              <h2 className="legal-h2">9. Changes to This Policy</h2>
              <ul className="legal-ul">
                <li>We may update this policy from time to time</li>
                <li>Continued use of the service after changes constitutes acceptance</li>
              </ul>
            </div>

            <div className="legal-divider" />

            <div id="section-10" className="legal-section">
              <h2 className="legal-h2">10. Contact</h2>
              <ul className="legal-ul">
                <li>Email: <a href="mailto:privacy@stackd.chat" className="legal-link">privacy@stackd.chat</a></li>
                <li>Website: <a href="https://stackd.chat" className="legal-link">stackd.chat</a></li>
              </ul>
            </div>
          </main>
        </div>

        <footer className="legal-footer">
          <span className="legal-footer-logo">stackd</span>
          <span className="legal-footer-copy">© 2026 stackd · <Link href="/terms" style={{ color: 'inherit', textDecoration: 'none' }}>Terms</Link> · <Link href="/stop" style={{ color: 'inherit', textDecoration: 'none' }}>Unsubscribe</Link></span>
        </footer>
      </div>
    </>
  );
}
