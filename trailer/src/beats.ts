// Hand-written 120 BPM grid: beat n at n * 0.5 - 0.06 s; no pulses in stops/silences.
export const FPS = 30;
export const DURATION = 2370;
export const f = (seconds: number) => Math.round(seconds * FPS);

const BEAT = 0.5;
const OFFSET = 0.06;
const LAST_BEAT = 152;
const SILENCES: ReadonlyArray<readonly [number, number]> = [
  [22.25, 24], [48.25, 49], [64.25, 65], [72.25, 73.75], [74.25, 79],
];
const MUFFLED: readonly [number, number] = [44, 48.25];

const silent = (seconds: number) => SILENCES.some(([a, b]) => seconds > a && seconds < b);

export const beats = Array.from({length: LAST_BEAT}, (_, i) => {
  const seconds = (i + 1) * BEAT - OFFSET;
  return {frame: f(seconds), seconds, strength: seconds > MUFFLED[0] && seconds < MUFFLED[1] ? 0.35 : 1};
}).filter(({seconds}) => !silent(seconds));

export const moments = {
  bigHit: f(16), stop: f(22.25), drop: f(24), breakSmall: f(43.25), muteStop: f(48.25),
  muteBack: f(49), weeksStop: f(64.25), minutesBack: f(65), silence: f(72.25),
  finalHit: f(73.75), musicEnd: f(76.05),
} as const;

export const cuts = {
  open: f(0), sintelOpen: f(4), scanner: f(8), lab: f(12), scannerWord: f(14), weeks: f(16),
  question: f(20), drop: f(24), hooked: f(28), drift: f(32), signals: f(36), groups: f(40),
  mute: f(44), compare: f(50), model: f(58), weeksAgain: f(62), minutes: f(65),
  title: f(73.75), credits: f(76), end: f(79),
} as const;

// HIT_OFFSET: seconds from the file's start to its main hit (measured from the files).
export const SFX = {
  impact: {file: 'sfx/impact.mp3', HIT_OFFSET: 0.35},
  mute: {file: 'sfx/mute_chime.wav', HIT_OFFSET: 0.28},
  scribble: {file: 'sfx/scribble.wav', HIT_OFFSET: 0},
} as const;
