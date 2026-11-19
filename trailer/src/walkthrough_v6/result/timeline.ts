import swee from '../../../public/walkthrough/swee/result.json';
import {Sfx, ShotCue, buildShots, wordFrame} from '../voice';

export {FPS} from '../common';
export const WIDTH = 1920;
export const HEIGHT = 1080;

export const OVERALL = swee.signals.overall as number[];
export const MUTED = swee.muted.overall as number[];
export const TO = OVERALL.length - 1;

// The peak comes from the data, never from the script's wording.
export const PEAK = OVERALL.reduce((best, value, second) => (value > OVERALL[best] ? second : best), 0);
export const PEAK_SCORE = OVERALL[PEAK];

const LOW = Math.min(...OVERALL);
const HIGH = Math.max(...OVERALL);
const PAD = (HIGH - LOW) * 0.12 || 0.04;
export const RANGE: [number, number] = [LOW - PAD, HIGH + PAD];

export const SHOTS = buildShots([
  {shot: '8a', ids: ['8.1']},
  {shot: '8b', ids: ['8.2']},
  {shot: '8c', ids: ['8.3'], sectionEnd: true},
]);
export const CUES = SHOTS.cues;
export const DURATION = SHOTS.duration;
export const LINES = CUES.flatMap((cue) => cue.lines);

export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot.slice(1), cue])) as Record<
  'a' | 'b' | 'c',
  ShotCue
>;
const word = (shot: keyof typeof SHOT, line: number, token: string, occurrence = 0, edge: 'start' | 'end' = 'start') =>
  wordFrame(SHOT[shot].lines[line], token, occurrence, edge);

// Moments keyed to words (frames in the result).
export const DASHED_IN = word('b', 0, 'dashed', 0, 'start'); // "The dashed line is the same video with the sound off"
export const PEAK_CLICK = word('c', 0, 'moment', 0, 'start'); // "Click any moment on the line" (it clicks 0:09, inside the report's 0:08-0:13 moment)
export const PEAK_MARK = PEAK_CLICK - 20; // the peak marker pops just before the click
export const CURSOR_IN = PEAK_CLICK - 34; // the cursor arrives, then clicks

export const RESULT_SFX: Sfx[] = [{frame: PEAK_CLICK, sound: 'click'}]; // the chart click
