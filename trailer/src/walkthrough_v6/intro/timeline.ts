import swee from '../../../public/walkthrough/swee/result.json';
import {ShotCue, buildShots, wordFrame} from '../voice';

export {FPS} from '../common';
export const WIDTH = 1920;
export const HEIGHT = 1080;

// Facts from the data and the product, not the script: the clip's real length and file name.
export const CLIP_SECONDS = swee.duration_seconds as number;
export const FILE_NAME = 'tiktok_ZSg5soxrR.mp4';

// The product charges credit in 30-second blocks (neurolens/pricing.py, 0.5 s allowance) and shows it
// as time ("uses 1 min"), so a 44 s clip is two blocks = 60 s of credit.
const BLOCK_S = 30;
const ALLOWANCE_S = 0.5;
export const CREDIT_SECONDS = Math.max(1, Math.ceil((CLIP_SECONDS - ALLOWANCE_S) / BLOCK_S)) * BLOCK_S;

export const SHOTS = buildShots([
  {shot: '1a', ids: ['1.1']},
  {shot: '1b', ids: ['1.2']},
  {shot: '1c', ids: ['1.3'], sectionEnd: true},
  {shot: '2a', ids: ['2.1']},
  {shot: '2b', ids: ['2.2']},
  {shot: '2c', ids: ['2.3'], sectionEnd: true},
]);
export const CUES = SHOTS.cues;
export const DURATION = SHOTS.duration;
export const LINES = CUES.flatMap((cue) => cue.lines);
export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot, cue])) as Record<
  '1a' | '1b' | '1c' | '2a' | '2b' | '2c',
  ShotCue
>;
const word = (shot: keyof typeof SHOT, token: string, occurrence = 0, edge: 'start' | 'end' = 'start') =>
  wordFrame(SHOT[shot].lines[0], token, occurrence, edge);

// Moments keyed to words (frames in the intro).
export const BRAIN_IN = word('1b', 'brain') - 10; // "how a brain will respond"
export const DROP = word('2b', 'it', 0, 'end'); // "Drop it in": the file lands
export const CREDIT_POP = word('2b', 'use') - 6; // "you see what it'll use"
export const HELD = word('2c', 'held'); // "held from your credit"
