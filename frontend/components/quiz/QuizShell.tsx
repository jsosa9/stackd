"use client";

import Navbar from '@/components/Navbar';

const BG    = '#0B0F14';

interface QuizShellProps {
  step: number;
  totalSteps: number;
  children: React.ReactNode;
}

export default function QuizShell({ step, totalSteps, children }: QuizShellProps) {
  return (
    <div style={{ minHeight: '100vh', background: BG, display: 'flex', flexDirection: 'column' }}>
      <Navbar step={step} totalSteps={totalSteps} />

      {/* Content */}
      <main className="flex-1 max-w-150 w-full mx-auto px-5 py-8">
        {children}
      </main>
    </div>
  );
}
