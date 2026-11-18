import swee from '../../../public/walkthrough/swee/result.json';
import {ShotCue, buildShots, wordFrame} from '../voice';

export {FPS} from '../common';
export const WIDTH = 1920;
export const HEIGHT = 1080;

export const OVERALL = swee.signals.overall as number[];
export const MUTED = swee.muted.overall as number[];
// The muted series is cut to the length both passes share, as the product does.
export const SHARED = Math.min(OVERALL.length, MUTED.length);
export const TO = SHARED - 1;

// The peak comes from the data, never from the script's wording.
export const PEAK = OVERALL.reduce((best, value, second) => (value > OVERALL[best] ? second : best), 0);
export const PEAK_SCORE = OVERALL[PEAK];

const ALL = [...OVERALL.slice(0, SHARED), ...MUTED.slice(0, SHARED)];
const LOW = Math.min(...ALL);
const HIGH = Math.max(...ALL);
const PAD = (HIGH - LOW) * 0.12 || 0.04;
export const RANGE: [number, number] = [LOW - PAD, HIGH + PAD];

export const SHOTS = buildShots([
  {shot: '8a', ids: ['8.1']},
  {shot: '8b', ids: ['8.2']},
  {shot: '8c', ids: ['8.3']},
  {shot: '8d', ids: ['8.4']},
  {shot: '8e', ids: ['8.5']},
  {shot: '8f', ids: ['8.6']},
  {shot: '8g', ids: ['8.7', '8.8'], sectionEnd: true},
]);
export const CUES = SHOTS.cues;
export const DURATION = SHOTS.duration;
export const LINES = CUES.flatMap((cue) => cue.lines);

export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot.slice(1), cue])) as Record<
  'a' | 'b' | 'c' | 'd' | 'e' | 'f' | 'g',
  ShotCue
>;
const word = (shot: keyof typeof SHOT, line: number, token: string, occurrence = 0, edge: 'start' | 'end' = 'start') =>
  wordFrame(SHOT[shot].lines[line], token, occurrence, edge);

// Moments keyed to words (frames in the result).
export const PEAK_MARK = word('d', 0, 'peaks'); // "It peaks at nine seconds"
export const CURSOR_IN = word('d', 0, 'seconds', 0, 'end'); // the cursor appears as the sentence ends
export const PEAK_CLICK = word('d', 0, 'peak', 0, 'start'); // "Click the peak"
export const DASHED = word('c', 0, 'dashed'); // "above the dashed line"
export const STRONGER = word('c', 0, 'stronger'); // "is a stronger response"
export const TOGGLE_IN = word('f', 0, 'autoplay'); // "autoplay on mute"
export const TOGGLE_CLICK = word('g', 0, 'off', 0, 'start'); // "Turn the sound off"
export const VOICE_LINE = SHOT.g.lines[1].start; // "Most of this response is coming from her voice"
