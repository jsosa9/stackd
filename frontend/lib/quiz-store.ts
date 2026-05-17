'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export interface CoachData {
  coachSetupMode: 'celebrity' | 'custom' | '';
  celebrityName: string;
  customCoachPersonalityDesc: string;
  coachTalkStyle: string[];
  coachMissBehavior: string;
  coachIntensity: number;
  customCoachAvoidPhrases: string;
  age: number | null;
}

const defaults: CoachData = {
  coachSetupMode: '',
  celebrityName: '',
  customCoachPersonalityDesc: '',
  coachTalkStyle: [],
  coachMissBehavior: '',
  coachIntensity: 3,
  customCoachAvoidPhrases: '',
  age: null,
};

interface QuizStore {
  data: CoachData;
  patch: (partial: Partial<CoachData>) => void;
  clear: () => void;
}

export const useQuizStore = create<QuizStore>()(
  persist(
    (set) => ({
      data: defaults,
      patch: (partial) => set((s) => ({ data: { ...s.data, ...partial } })),
      clear: () => set({ data: defaults }),
    }),
    {
      name: 'stackd_coach',
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
);
