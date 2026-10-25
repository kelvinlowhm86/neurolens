import {f} from './beats';

export type Cue = {id: string; text: string; start: number; end: number};

const SIGNAL_NAMES = ['Faces', 'Bodies', 'Scenes', 'Visual', 'Sound', 'Speech', 'Social'] as const;


export const CREDIT_LINES = [
  'Predicted brain response from TRIBE v2 (Meta, CC BY-NC).',
  'Big Buck Bunny and Sintel \u00a9 Blender Foundation, CC BY 3.0.',
  'Music and sound effects made with ElevenLabs.',
] as const;

const cue = (id: string, text: string, start: number, end: number): Cue => ({
  id, text, start: f(start), end: f(end),
});

export const CUE = {
  openA: cue('open-a', 'You made a video.', 0, 3),
  openB: cue('open-b', 'Will it grab them?', 3, 6),
  answer: cue('answer', 'The real answer is in the brain.', 6, 8),
  build: [
    cue('build-lab', 'A lab.', 8, 10),
    cue('build-scanner', 'A scanner.', 10, 12),
    cue('build-weeks', 'Weeks.', 12, 14),
  ],
  stopA: cue('stop-a', 'What if all you needed', 14, 18.25),
  stopB: cue('stop-b', 'was the video?', 18.25, 20),
  drop: cue('drop', 'Meet NeuroLens.', 20.5, 24),
  hooked: cue('hooked', 'The second they\u2019re hooked.', 24, 28),
  drift: cue('drift', 'The second they drift.', 28, 32),
  signalNames: SIGNAL_NAMES.map((text, i) =>
    cue(`signal-${text.toLowerCase()}`, text, 32 + i * 0.5, 36),
  ),
  groups: [
    cue('group-see', 'See', 36, 40),
    cue('group-hear', 'Hear', 36, 40),
    cue('group-understand', 'Understand', 36, 40),
  ],
  mute: cue('mute', 'Still works on mute?', 40.3, 46),
  muteAnswer: cue('mute-answer', 'Yes.', 44.25, 46),
  compare: cue('compare', 'Which opening wins?', 46, 54),
  model: cue('model', 'Built on a model trained on real brain scans.', 54, 58),
  weeks: cue('weeks', 'Weeks', 58, 61),
  minutes: cue('minutes', 'Minutes.', 61, 68.25),
  endingTitle: cue('ending-title', 'NeuroLens', 69.75, 72),
  endingSub: cue('ending-sub', 'See how your video lands.', 70.5, 72),
  credits: cue('credits', CREDIT_LINES.join(' '), 72, 75),
} as const;

export const LABEL = {
  bunny: 'Big Buck Bunny',
  sintel: 'Sintel',
  openingA: 'Opening A',
  openingB: 'Opening B',
  peak: 'peak',
  trough: 'low',
  sameScale: 'same signed scale',
  withAudio: 'With sound',
  mutedLabel: 'On mute',
  typical: 'typical viewing',
  seconds: 's',
  signals: SIGNAL_NAMES,
} as const;
