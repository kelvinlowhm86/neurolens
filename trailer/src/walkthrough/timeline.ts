import voiceFile from '../../public/walkthrough/audio/voice_lines_v4.json';

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;
export const FADE = 12;

export const sec = (s: number) => Math.round(s * FPS);

export type VoiceLine = {line: number; start: number; end: number; text: string};
export const LINES = voiceFile.lines as VoiceLine[];
export const VOICE_SOURCE = voiceFile.source;

const findLine = (n: number): VoiceLine => {
  const line = LINES.find((l) => l.line === n);
  if (!line) throw new Error(`Voice line ${n} is missing from voice_lines_v4.json`);
  return line;
};

export const at = (n: number, offset = 0) => findLine(n).start + offset;

export const LAST_END = Math.max(...LINES.map((l) => l.end));
// The close keeps the wordmark and the brain on screen after the last word.
export const CLOSE_HOLD = 8;
export const DURATION_S = LAST_END + CLOSE_HOLD;
export const DURATION = sec(DURATION_S);

// The track is 144.72 s. Starting it late makes its own ending land on the last frame.
export const MUSIC_TRACK_S = 144.72;
export const MUSIC_OFFSET = Math.max(0, MUSIC_TRACK_S - DURATION_S);

const LEAD = 0.3;
export const SCENES = {
  opening: {start: 0, end: at(2, -LEAD)},
  upload: {start: at(2, -LEAD), end: at(4, -LEAD)},
  journey: {start: at(4, -LEAD), end: at(14, -LEAD)},
  result: {start: at(14, -LEAD), end: at(21, -LEAD)},
  built: {start: at(21, -LEAD), end: at(23, -LEAD)},
  close: {start: at(23, -LEAD), end: DURATION_S},
};

export const JOURNEY = {
  start: SCENES.journey.start,
  browser: at(4),
  s3: at(4, 0.9),
  website: at(5),
  queue: at(6, 0.5),
  check: at(6, 3.2),
  ticks: at(6, 4.0),
  gpuIdle: at(7),
  gpuWake: at(8, 3.1),
  brain: at(9),
  secondLine: at(10, 0.35),
  saved: at(11),
  report: at(12),
  off: at(13),
  end: SCENES.journey.end,
};

export const RESULT = {
  start: SCENES.result.start,
  cameBack: at(14),
  chart: at(15),
  report: at(16),
  photos: at(17),
  mute: at(18),
  alley: at(19),
  compare: at(20),
  end: SCENES.result.end,
};

export const BUILT = {
  start: SCENES.built.start,
  terraform: at(21),
  watchdog: at(22),
  end: SCENES.built.end,
};

export const CLOSE = {start: SCENES.close.start, end: SCENES.close.end};

export const SWEE = {
  video: 'walkthrough/swee/tiktok_ZSg5soxrR.mp4',
  brain: 'walkthrough/swee/brain',
  result: 'walkthrough/swee/result.json',
  duration: 44,
};

export const SFX = {
  tap: 'walkthrough/sfx/tap.mp3',
  whoosh: 'walkthrough/sfx/whoosh.mp3',
  pop: 'walkthrough/sfx/pop.mp3',
  resolve: 'walkthrough/sfx/resolve.mp3',
  mute: 'sfx/mute_chime.wav',
};

export type SfxCue = {at: number; src: string; volume: number};

const cue = (time: number, src: string, volume: number): SfxCue => ({at: Math.max(0, time), src, volume});
export const SFX_CUES: SfxCue[] = [
  cue(at(2, 0.5), SFX.tap, 0.22),
  cue(at(3, 0.2), SFX.pop, 0.12),
  cue(JOURNEY.start + 0.2, SFX.whoosh, 0.22),
  cue(JOURNEY.queue - 0.1, SFX.pop, 0.12),
  cue(JOURNEY.gpuWake, SFX.pop, 0.16),
  cue(JOURNEY.saved, SFX.tap, 0.18),
  cue(JOURNEY.report, SFX.tap, 0.14),
  cue(RESULT.start + 0.2, SFX.whoosh, 0.2),
  cue(RESULT.mute + 2.2, SFX.mute, 0.24),
  cue(RESULT.compare, SFX.whoosh, 0.16),
  cue(BUILT.start + 0.2, SFX.whoosh, 0.2),
  cue(BUILT.watchdog, SFX.tap, 0.16),
  cue(CLOSE.start + 1.0, SFX.resolve, 0.3),
];

export type MapCard = {
  id: string; title: string; label: string;
  x: number; y: number; w: number; h: number;
};

export const MAP: Record<string, MapCard> = {
  browser: {id: 'browser', title: 'Browser', label: 'your file', x: 100, y: 300, w: 300, h: 170},
  s3: {id: 's3', title: 'Storage', label: 'S3', x: 560, y: 300, w: 300, h: 170},
  website: {id: 'website', title: 'Website', label: 'CloudFront + Lambda', x: 560, y: 560, w: 320, h: 170},
  queue: {id: 'queue', title: 'Queue', label: 'SQS', x: 1080, y: 300, w: 300, h: 170},
  check: {id: 'check', title: 'Check', label: 'Lambda · length + price', x: 1560, y: 300, w: 300, h: 170},
  gpu: {id: 'gpu', title: 'GPU', label: 'idle: $0 / hour', x: 2060, y: 280, w: 340, h: 210},
  brain: {id: 'brain', title: 'Brain', label: 'TRIBE v2', x: 2060, y: 560, w: 620, h: 460},
  response: {id: 'response', title: 'Response', label: 'second by second', x: 2760, y: 560, w: 620, h: 460},
  credits: {id: 'credits', title: 'Credit', label: 'held → charged', x: 1560, y: 560, w: 300, h: 170},
  report: {id: 'report', title: 'Report', label: 'Bedrock · Claude', x: 2900, y: 300, w: 360, h: 190},
  watchdog: {id: 'watchdog', title: 'Watchdog', label: 'every 10 min', x: 1080, y: 560, w: 300, h: 170},
};

export const MAP_W = 3500;
export const MAP_H = 1080;


// Visual layers are contiguous; the fade at each join is the only overlap.
export function checkCoverage() {
  const layers = Object.entries(SCENES).map(([id, s]) => ({id, ...s}));
  for (let i = 0; i < layers.length; i++) {
    for (let j = i + 1; j < layers.length; j++) {
      const a = layers[i];
      const b = layers[j];
      const overlap = Math.min(a.end, b.end) - Math.max(a.start, b.start);
      if (overlap > 0.05) throw new Error(`Scenes overlap: ${a.id} and ${b.id}`);
    }
  }
}

checkCoverage();
