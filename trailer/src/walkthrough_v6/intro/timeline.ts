import swee from '../../../public/walkthrough/swee/result.json';
import {Line, buildTimeline} from '../common';

export {FPS} from '../common';
export const WIDTH = 1920;
export const HEIGHT = 1080;

// Facts from the data and the product, not the script: the clip's real length and file name.
export const CLIP_SECONDS = swee.duration_seconds as number;
export const FILE_NAME = 'tiktok_ZSg5soxrR.mp4';

// The product prices in 30-second blocks (neurolens/pricing.py, 0.5 s allowance) and shows credit
// as time ("uses 1 min"), so a 44 s clip is two blocks = 60 s of credit.
const BLOCK_S = 30;
const ALLOWANCE_S = 0.5;
export const CREDIT_SECONDS = Math.max(1, Math.ceil((CLIP_SECONDS - ALLOWANCE_S) / BLOCK_S)) * BLOCK_S;

// Scratch timings (2.6 words per second plus a 0.5 s hold). Replace the cues with the real voice later.
export const LINES: Line[] = [
  ['1a', 'You made a video.'],
  ['1b', 'NeuroLens predicts how a brain will respond to it, second by second.'],
  ['1c', "Let's put one through."],
  ['2a', 'This is a TikTok from Swee Choon, a dim sum restaurant.'],
  ['2b', 'Drop it in, and you see the price before anything runs.'],
  ['2c', 'That amount is held from your credit, not spent.'],
];

const built = buildTimeline(LINES);
export const CUES = built.cues;
export const DURATION = built.duration;
export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot, cue])) as Record<
  '1a' | '1b' | '1c' | '2a' | '2b' | '2c',
  (typeof CUES)[number]
>;
// The frame to still for each shot: just before its cut.
export const HOLD_FRAMES = Object.fromEntries(CUES.map((cue) => [cue.shot, cue.end - 8]));
