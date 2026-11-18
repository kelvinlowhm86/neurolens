import report from '../swee_report.json';
import {ShotCue, buildShots, wordFrame} from '../voice';

export {FPS} from '../common';
export const WIDTH = 1920;
export const HEIGHT = 1080;

// The report text is the real one from data/samples.json (sample tiktok_ZSg5soxrR), copied to swee_report.json.
export const REPORT = report;

export const SHOTS = buildShots([
  {shot: '9a', ids: ['9.1']},
  {shot: '9b', ids: ['9.2']},
  {shot: '9c', ids: ['9.3']},
  {shot: '9d', ids: ['9.4', '9.5'], sectionEnd: true},
  {shot: '10a', ids: ['10.1']},
  {shot: '10b', ids: ['10.2'], sectionEnd: true},
  {shot: '11', ids: ['11.1']},
]);
export const CUES = SHOTS.cues;
// The closing frame holds 1.5 s after the voice's hold, then fades to black.
export const DURATION = SHOTS.duration + 45;
export const LINES = CUES.flatMap((cue) => cue.lines);
export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot, cue])) as Record<
  '9a' | '9b' | '9c' | '9d' | '10a' | '10b' | '11',
  ShotCue
>;
// The three rows of the moment appear as the voice names them: "what's on screen, what the brain is doing, and what to change".
const nameWord = (token: string) => wordFrame(SHOT['9b'].lines[0], token);
export const ROW_IN = [nameWord('screen') - 14, nameWord('brain') - 14, nameWord('change') - 14];
export const HOLD_FRAMES = Object.fromEntries(CUES.map((cue) => [cue.shot, cue.end - 8]));
