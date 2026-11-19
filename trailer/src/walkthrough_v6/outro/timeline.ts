import report from '../swee_report.json';
import {ShotCue, buildShots, wordFrame} from '../voice';

export {FPS} from '../common';
export const WIDTH = 1920;
export const HEIGHT = 1080;

// The report text is the real one from data/samples.json (sample tiktok_ZSg5soxrR), copied to swee_report.json.
export const REPORT = report;

export const SHOTS = buildShots([
  {shot: '9a', ids: ['9.1']},
  {shot: '9b', ids: ['9.2'], sectionEnd: true},
  {shot: '10a', ids: ['10.1']},
  {shot: '10b', ids: ['10.2'], sectionEnd: true},
  {shot: '11', ids: ['11.1']},
]);
export const CUES = SHOTS.cues;
// The closing frame holds 3 s after the voice's hold while the music comes up, then fades to black.
export const DURATION = SHOTS.duration + 90;
export const LINES = CUES.flatMap((cue) => cue.lines);
export const SHOT = Object.fromEntries(CUES.map((cue) => [cue.shot, cue])) as Record<
  '9a' | '9b' | '10a' | '10b' | '11',
  ShotCue
>;
// The three rows of the moment appear as the voice goes: "writes it up", "moment by moment", "what to change".
const nameWord = (token: string) => wordFrame(SHOT['9a'].lines[0], token);
export const ROW_IN = [nameWord('up') - 14, nameWord('moment') - 14, nameWord('change') - 14];
// "Claude, on Amazon Bedrock": the label appears as the voice says Claude.
export const BEDROCK_IN = nameWord('Claude') - 6;
// 9.2 names the sound-off finding: the card's caption appears as the voice says "dashed line".
export const SOUND_OFF_IN = wordFrame(SHOT['9b'].lines[0], 'dashed') - 6;
// 10: the counter's number is unknowable, so its "?" lands on "views". The clip plays on from a few
// seconds before the report's moment (8-13 s), so the playhead glides into it with no jump.
export const VIEW_QUESTION_IN = wordFrame(SHOT['10a'].lines[0], 'views');
export const CURSOR_FROM = REPORT.moment.start;
export const CURSOR_TO = REPORT.moment.end;
export const CLIP_START_SECOND = REPORT.moment.start - 3;
export const CLOSE_START = SHOT['11'].start;
export const HOLD_FRAMES = Object.fromEntries(CUES.map((cue) => [cue.shot, cue.end - 8]));
