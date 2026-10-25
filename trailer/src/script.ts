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

const openSplit = Math.min(
  cuts.build - 1,
  Math.max(cuts.open + 1, nearest(downbeatFrames.filter((f) => f > cuts.open && f < cuts.build), (cuts.open + cuts.build) / 2)),
);

const buildDownbeats = downbeatFrames.filter((f) => f >= cuts.build && f < cuts.stop);
const buildWordStarts = buildDownbeats.length >= 3
  ? buildDownbeats.slice(0, 3)
  : pick(cuts.build, cuts.stop, 3);

const stopBeats = downbeatFrames.filter((f) => f > cuts.stop && f < cuts.drop);
const stopSplit = stopBeats[0] ?? Math.round((cuts.stop + cuts.drop) / 2);

const signalStarts = pick(cuts.signals, cuts.muted, 7);
const endingSubStart = nextBeat(moments.endingHit, 1);

export const CUE = {
  openA: cue('open-a', 'To see how a brain reacts to a video,', cuts.open, openSplit),
  openB: cue('open-b', 'you needed this.', openSplit, cuts.build),
  build: [
    cue('build-lab', 'A lab.', buildWordStarts[0], buildWordStarts[1]),
    cue('build-scanner', 'A scanner.', buildWordStarts[1], buildWordStarts[2]),
    cue('build-weeks', 'Weeks.', buildWordStarts[2], cuts.stop),
  ],
  stopA: cue('stop-a', 'What if all you needed', cuts.stop, stopSplit),
  stopB: cue('stop-b', 'was the video?', stopSplit, cuts.drop),
  drop: cue('drop', 'Meet NeuroLens.', nextBeat(cuts.drop, 1), cuts.hooked),
  hooked: cue('hooked', 'The second they\u2019re hooked.', cuts.hooked, cuts.drift),
  drift: cue('drift', 'The second they drift.', cuts.drift, cuts.signals),
  signals: cue('signals', 'Seven signals.', cuts.signals, cuts.muted),
  signalNames: SIGNAL_NAMES.map((text, i) =>
    cue(`signal-${text.toLowerCase()}`, text, signalStarts[i], cuts.muted),
  ),
  muted: cue('muted', 'Still works on mute?', cuts.muted, cuts.compare),
  compare: cue('compare', 'Which cut wins?', cuts.compare, cuts.model),
  model: cue('model', 'Built on a model trained on real brain scans.', cuts.model, cuts.ending),
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
