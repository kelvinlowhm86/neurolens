import swee from '../../../public/walkthrough/swee/result.json';

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

export const OVERALL = swee.signals.overall as number[];
export const MUTED = swee.muted.overall as number[];
export const SHARED = Math.min(OVERALL.length, MUTED.length);
export const TO = SHARED - 1;

export const PEAK = OVERALL.reduce((best, value, second) => (value > OVERALL[best] ? second : best), 0);
export const PEAK_SCORE = OVERALL[PEAK];

const ALL = [...OVERALL.slice(0, SHARED), ...MUTED.slice(0, SHARED)];
const LOW = Math.min(...ALL);
const HIGH = Math.max(...ALL);
const PAD = (HIGH - LOW) * 0.12 || 0.04;
export const RANGE: [number, number] = [LOW - PAD, HIGH + PAD];

export const CHART = {x: 60, y: 612, w: 1800, h: 360};

const A = 96;
const B = 177;
const C = 153;
const D = 188;
const E = 96;
const F = 107;
const G = 238;

export const SHOT = {
  a: {start: 0, end: A},
  b: {start: A, end: A + B},
  c: {start: A + B, end: A + B + C},
  d: {start: A + B + C, end: A + B + C + D},
  e: {start: A + B + C + D, end: A + B + C + D + E},
  f: {start: A + B + C + D + E, end: A + B + C + D + E + F},
  g: {start: A + B + C + D + E + F, end: A + B + C + D + E + F + G},
};

export const PEAK_CLICK = SHOT.d.start + 150;
export const TOGGLE_CLICK = SHOT.f.start + 60;

export const DURATION = SHOT.g.end + 15;

export const TIMING = [
  {shot: '8a', words: 7, start: SHOT.a.start, end: SHOT.a.end},
  {shot: '8b', words: 14, start: SHOT.b.start, end: SHOT.b.end},
  {shot: '8c', words: 12, start: SHOT.c.start, end: SHOT.c.end},
  {shot: '8d', words: 15, start: SHOT.d.start, end: SHOT.d.end},
  {shot: '8e', words: 7, start: SHOT.e.start, end: SHOT.e.end},
  {shot: '8f', words: 8, start: SHOT.f.start, end: SHOT.f.end},
  {shot: '8g', words: 18, start: SHOT.g.start, end: SHOT.g.end},
];
