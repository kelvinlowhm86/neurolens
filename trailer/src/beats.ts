// Hand-written 120 BPM grid: beat n at n * 0.5 - 0.06 s in music time; no pulses in stops/silences.
export const FPS = 30;
export const DURATION = 2250;
export const f = (seconds: number) => Math.round(seconds * FPS);

// Both music beds play from this point into the uncut files.
export const MUSIC_START = 4;

const BEAT = 0.5;
const OFFSET = 0.06;
const LAST_BEAT = 152;

// Music time to trailer time.
const at = (musicSeconds: number) => musicSeconds - MUSIC_START;

export const moments = {
  bigHit: f(at(16)), stop: f(at(22.25)), drop: f(at(24)), breakSmall: f(at(43.25)),
  muteStop: f(at(48.25)), muteBack: f(at(49)), weeksStop: f(at(64.25)), minutesBack: f(at(65)),
  silence: f(at(72.25)), finalHit: f(at(73.75)), musicEnd: f(at(76.05)),
} as const;

const SILENCES: ReadonlyArray<readonly [number, number]> = [
  [at(22.25), at(24)], [at(48.25), at(49)], [at(64.25), at(65)],
  [at(72.25), at(73.75)], [at(74.25), DURATION / FPS],
];
const MUFFLED: readonly [number, number] = [at(44), at(48.25)];

const silent = (seconds: number) => SILENCES.some(([a, b]) => seconds > a && seconds < b);

export const beats = Array.from({length: LAST_BEAT}, (_, i) => {
  const seconds = at((i + 1) * BEAT - OFFSET);
  return {frame: f(seconds), seconds, strength: seconds > MUFFLED[0] && seconds < MUFFLED[1] ? 0.35 : 1};
}).filter(({seconds}) => seconds >= 0 && !silent(seconds));

export const CLIPS = {
  openBunny: 153 / 30, openSintel: 12,
  hooked: [48.5, 52.5], drift: [26.5, 30.5], mute: [18, 24],
  compareA: [1, 9], compareB: [22, 30],
} as const;

export const cuts = {
  open: f(0), sintelOpen: f(3), scanner: f(6), lab: f(8), scannerWord: f(10), weeks: f(12),
  question: f(14), drop: f(20), hooked: f(24), drift: f(28), signals: f(32), groups: f(36),
  mute: f(40), compare: f(46), model: f(54), weeksAgain: f(58), minutes: f(61),
  title: moments.finalHit, credits: f(72), end: DURATION,
} as const;

// HIT_OFFSET: seconds from the file's start to its main hit (measured from the files).
export const SFX = {
  impact: {file: 'sfx/impact.mp3', HIT_OFFSET: 0.35},
  mute: {file: 'sfx/mute_chime.wav', HIT_OFFSET: 0.28},
  scribble: {file: 'sfx/scribble.wav', HIT_OFFSET: 0},
} as const;
