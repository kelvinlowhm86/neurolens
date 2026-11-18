import report from '../swee_report.json';
import {Line, buildTimeline} from '../common';

export {FPS} from '../common';
export const WIDTH = 1920;
export const HEIGHT = 1080;

// The report text is the real one from data/samples.json (sample tiktok_ZSg5soxrR), copied to swee_report.json.
export const REPORT = report;

export const LINES: Line[] = [
  ['9a', 'Then the report reads the whole thing for you, in plain words.'],
  ['9b', "It goes moment by moment. What's on screen, what the brain is doing, and what to change."],
  ['9c', "It's written by Claude, on Amazon Bedrock."],
  ['9d', 'For the old photos, it suggests cutting in a second of her face, reacting. A face holds attention, even with the sound off.'],
  ['10a', 'None of this predicts your views.'],
  ['10b', 'It shows how a brain is likely to respond, second by second, so you can fix things before you post.'],
  ['11', 'The samples are live. Explore them at the link below.'],
];

const built = buildTimeline(LINES);
export const CUES = built.cues;
// The closing frame holds 1.5 s after the voice, then fades to black.
export const DURATION = built.duration + 45;
export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot, cue])) as Record<
  '9a' | '9b' | '9c' | '9d' | '10a' | '10b' | '11',
  (typeof CUES)[number]
>;
export const HOLD_FRAMES = Object.fromEntries(CUES.map((cue) => [cue.shot, cue.end - 8]));
