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
  openA: cue('open-a', 'You made a video.', 0, 4),
  openB: cue('open-b', 'Will it grab them?', 4, 8),
  answer: cue('answer', 'The real answer is in the brain.', 8, 12),
  build: [
    cue('build-lab', 'A lab.', 12, 14),
    cue('build-scanner', 'A scanner.', 14, 16),
    cue('build-weeks', 'Weeks.', 16, 20),
  ],
  stopA: cue('stop-a', 'What if all you needed', 20, 22.25),
  stopB: cue('stop-b', 'was the video?', 22.25, 24),
  drop: cue('drop', 'Meet NeuroLens.', 24.5, 28),
  hooked: cue('hooked', 'The second they\u2019re hooked.', 28, 32),
  drift: cue('drift', 'The second they drift.', 32, 36),
  signalNames: SIGNAL_NAMES.map((text, i) =>
    cue(`signal-${text.toLowerCase()}`, text, 36 + i * 0.5, 40),
  ),
  groups: [
    cue('group-see', 'See', 40, 44),
    cue('group-hear', 'Hear', 40, 44),
    cue('group-understand', 'Understand', 40, 44),
  ],
  mute: cue('mute', 'Still works on mute?', 44.3, 50),
  compare: cue('compare', 'Which cut wins?', 50, 58),
  model: cue('model', 'Built on a model trained on real brain scans.', 58, 62),
  weeks: cue('weeks', 'Weeks', 62, 65),
  minutes: cue('minutes', 'minutes.', 65, 72.25),
  endingTitle: cue('ending-title', 'NeuroLens', 73.75, 76),
  endingSub: cue('ending-sub', 'See how your video lands.', 74.5, 76),
  credits: cue('credits', CREDIT_LINES.join(' '), 76, 79),
} as const;

export const LABEL = {
  bunny: 'Big Buck Bunny',
  sintel: 'Sintel',
  peak: 'peak',
  trough: 'low',
  sameScale: 'same signed scale',
  withAudio: 'With sound',
  mutedLabel: 'On mute',
  typical: 'typical viewing',
  seconds: 's',
  signals: SIGNAL_NAMES,
} as const;
