import swee from '../../../public/walkthrough/swee/result.json';
import {Line, buildTimeline} from '../common';

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

// Scratch timings (2.6 words per second plus a 0.5 s hold). The real voice replaces these lines' lengths.
export const LINES: Line[] = [
  ['8a', "About ten minutes later, here's the result."],
  ['8b', "The line shows how strong the response is. The brain shows where it's happening."],
  ['8c', 'Anything above the dashed line is a stronger response than ordinary viewing.'],
  ['8d', 'It peaks at eight seconds. Click the peak, and the video jumps right to it.'],
  ['8e', "She's telling the story over old photos."],
  ['8f', 'But a lot of feeds autoplay on mute.'],
  ['8g', 'Turn the sound off, and the whole line drops. Most of this response is coming from her voice.'],
];

const built = buildTimeline(LINES);
export const CUES = built.cues;
export const DURATION = built.duration + 15;

export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot.slice(1), cue])) as Record<
  'a' | 'b' | 'c' | 'd' | 'e' | 'f' | 'g',
  (typeof CUES)[number]
>;

export const PEAK_CLICK = SHOT.d.start + 112;
export const TOGGLE_CLICK = SHOT.f.start + 66;
