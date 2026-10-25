import {FPS, DURATION, beats, downbeats, moments, cuts} from './beats';

type Beat = {frame: number; seconds?: number; strength?: number};
export type Cue = {id: string; text: string; start: number; end: number};

const frames = (list: readonly Beat[]) =>
  Array.from(new Set(list.map((b) => Math.round(b.frame)))).sort((a, b) => a - b);

export const beatFrames = frames(beats);
export const downbeatFrames = frames(downbeats).length
  ? frames(downbeats)
  : beatFrames.filter((_, i) => i % 4 === 0);

const nearest = (list: number[], target: number) =>
  list.length ? list.reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a)) : Math.round(target);

const nextBeat = (frame: number, count = 1) => {
  const start = beatFrames.findIndex((f) => f >= frame);
  if (start < 0) return frame;
  return beatFrames[Math.min(start + count, beatFrames.length - 1)];
};

const between = (a: number, b: number) => beatFrames.filter((f) => f >= a && f < b);

const pick = (a: number, b: number, count: number) => {
  const found = between(a, b);
  return Array.from({length: count}, (_, i) => {
    const ideal = a + ((b - a) * (i + 0.5)) / count;
    return nearest(found.length ? found : beatFrames, ideal);
  });
};

const SIGNAL_NAMES = ['Faces', 'Bodies', 'Scenes', 'Visual', 'Sound', 'Speech', 'Social'] as const;

export const CREDIT_LINES = [
  'Predicted brain response from TRIBE v2 (Meta, CC BY-NC).',
  'Big Buck Bunny and Sintel \u00a9 Blender Foundation, CC BY 3.0.',
  'Music: LNPlusMusic (Pixabay).',
] as const;

const cue = (id: string, text: string, start: number, end: number): Cue => ({
  id,
  text,
  start: Math.max(0, Math.round(start)),
  end: Math.min(DURATION, Math.max(Math.round(start) + 1, Math.round(end))),
});

const openSplit = nearest(downbeatFrames.filter((f) => f > cuts.open && f < cuts.build), (cuts.open + cuts.build) / 2);

// Build: the answer line on its first bar, three punch words one per beat, then the question
// starts on the last downbeat so "was the video?" lands in the music's stop.
const buildBars = downbeatFrames.filter((f) => f >= cuts.build && f < cuts.stop);
const answerEnd = buildBars[1] ?? Math.round((cuts.build + cuts.stop) / 2);
const questionStart = buildBars[buildBars.length - 1] ?? cuts.stop;
const buildWordStarts = pick(answerEnd, questionStart, 3);

// After the compare: the model line, then "Weeks" struck through and "minutes." on the next bar,
// then the title on the ending hit.
const lateBars = downbeatFrames.filter((f) => f > cuts.model && f < moments.endingHit);
const minutesStart = lateBars[lateBars.length - 2] ?? moments.endingHit - 72;
const weeksStart = lateBars[lateBars.length - 3] ?? minutesStart - 72;

const signalStarts = pick(cuts.signals, cuts.muted, 7);
const endingSubStart = nextBeat(moments.endingHit, 1);

export const CUE = {
  openA: cue('open-a', 'You made a video.', cuts.open, openSplit),
  openB: cue('open-b', 'Will it grab them?', openSplit, cuts.build),
  answer: cue('answer', 'The real answer is in the brain.', cuts.build, answerEnd),
  build: [
    cue('build-lab', 'A lab.', buildWordStarts[0], buildWordStarts[1]),
    cue('build-scanner', 'A scanner.', buildWordStarts[1], buildWordStarts[2]),
    cue('build-weeks', 'Weeks.', buildWordStarts[2], questionStart),
  ],
  stopA: cue('stop-a', 'What if all you needed', questionStart, cuts.drop),
  stopB: cue('stop-b', 'was the video?', cuts.stop, nextBeat(cuts.drop, 1)),
  drop: cue('drop', 'Meet NeuroLens.', nextBeat(cuts.drop, 1), cuts.hooked),
  hooked: cue('hooked', 'The second they\u2019re hooked.', cuts.hooked, cuts.drift),
  drift: cue('drift', 'The second they drift.', cuts.drift, cuts.signals),
  signals: cue('signals', 'Seven signals.', cuts.signals, cuts.muted),
  signalNames: SIGNAL_NAMES.map((text, i) =>
    cue(`signal-${text.toLowerCase()}`, text, signalStarts[i], cuts.muted),
  ),
  muted: cue('muted', 'Still works on mute?', cuts.muted, cuts.compare),
  compare: cue('compare', 'Which cut wins?', cuts.compare, cuts.model),
  model: cue('model', 'Built on a model trained on real brain scans.', cuts.model, weeksStart),
  weeks: cue('weeks', 'Weeks', weeksStart, moments.endingHit),
  minutes: cue('minutes', 'minutes.', minutesStart, moments.endingHit),
  endingTitle: cue('ending-title', 'NeuroLens', moments.endingHit, cuts.end),
  endingSub: cue('ending-sub', 'See how your video lands.', endingSubStart, cuts.end),
  credits: cue('credits', CREDIT_LINES.join(' '), cuts.credits, cuts.end),
} as const;


export const LABEL = {
  bunny: 'Big Buck Bunny',
  sintel: 'Sintel',
  peak: 'peak',
  trough: 'trough',
  sameScale: 'same signed scale',
  withAudio: 'With audio',
  mutedLabel: 'Muted',
  seconds: 's',
  signals: SIGNAL_NAMES,
} as const;

export {FPS, DURATION, beats, downbeats, moments, cuts};
