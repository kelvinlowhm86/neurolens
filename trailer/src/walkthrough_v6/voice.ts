import lines from '../../public/walkthrough/v6voice/v6_lines.json';

// The voice drives every timeline. public/walkthrough/v6voice/ holds one wav per script line plus
// v6_lines.json ({id, text, duration}). A new voice is a file swap: same ids, new durations.
export const FPS = 30;
export const LINE_GAP = 0.4; // seconds of silence between two lines of the same shot
export const SHOT_HOLD = 0.5; // seconds a shot holds after its last word
export const SECTION_HOLD = 1.2; // the longer pause after the last shot of a section

type VoiceLine = {id: string; text: string; duration: number};
const VOICE = new Map((lines as VoiceLine[]).map((line) => [line.id, line]));

const voiceLine = (id: string): VoiceLine => {
  const line = VOICE.get(id);
  if (!line) throw new Error(`v6_lines.json has no line "${id}"`);
  return line;
};

export type LineCue = {id: string; text: string; start: number; frames: number};
export type ShotCue = {shot: string; start: number; end: number; lines: LineCue[]};
// `hold` (seconds) overrides the pause after the shot's last word, e.g. a wordless music moment.
export type ShotSpec = {shot: string; ids: string[]; sectionEnd?: boolean; hold?: number};

/** Back-to-back shots from the real line lengths. A shot starts on its first word and holds after its last. */
export const buildShots = (specs: ShotSpec[]): {cues: ShotCue[]; duration: number} => {
  let cursor = 0;
  const cues = specs.map(({shot, ids, sectionEnd, hold}) => {
    const start = cursor;
    const cueLines = ids.map((id, i) => {
      const {text, duration} = voiceLine(id);
      if (i > 0) cursor += Math.round(LINE_GAP * FPS);
      const cue = {id, text, start: cursor, frames: Math.round(duration * FPS)};
      cursor += cue.frames;
      return cue;
    });
    cursor += Math.round((hold ?? (sectionEnd ? SECTION_HOLD : SHOT_HOLD)) * FPS);
    return {shot, start, end: cursor, lines: cueLines};
  });
  return {cues, duration: cursor};
};

const normalise = (word: string) => word.toLowerCase().replace(/[^a-z0-9'-]/g, '');

// Estimated word times inside a line: the line's length shared out by letters, with a short pause
// after commas and a longer one after sentence ends. No speech recogniser: an estimate.
const wordTimes = (text: string, duration: number) => {
  const raw = text.trim().split(/\s+/);
  const weights = raw.map((word) => {
    const letters = normalise(word).length + 1.5;
    const pause = /[.?!]$/.test(word) ? 4 : /[,;:]$/.test(word) ? 2 : 0;
    return {word: normalise(word), body: letters, pause};
  });
  const total = weights.reduce((sum, w) => sum + w.body + w.pause, 0);
  let at = 0;
  return weights.map((w) => {
    const start = (at / total) * duration;
    const end = ((at + w.body) / total) * duration;
    at += w.body + w.pause;
    return {word: w.word, start, end};
  });
};

/** The frame (in the section) a word starts or ends. `occurrence` counts repeats of the word in the line. */
export const wordFrame = (line: LineCue, word: string, occurrence = 0, edge: 'start' | 'end' = 'start'): number => {
  const target = normalise(word);
  const hits = wordTimes(line.text, line.frames / FPS).filter((w) => w.word === target);
  const hit = hits[occurrence];
  if (!hit) throw new Error(`line ${line.id} has no word "${word}" (occurrence ${occurrence})`);
  return line.start + Math.round((edge === 'start' ? hit.start : hit.end) * FPS);
};

/** A fraction of the way through a line's speech. */
export const speechFrame = (line: LineCue, progress: number) => line.start + Math.round(line.frames * progress);

// A sound effect at a comp-local frame; Full.tsx places and mixes them.
export type SfxName = 'click' | 'tick' | 'scribble' | 'denied' | 'stamp';
export type Sfx = {frame: number; sound: SfxName};
