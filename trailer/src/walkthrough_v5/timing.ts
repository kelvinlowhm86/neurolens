import voiceFile from '../../public/walkthrough/v5/voice_lines_v5.json';
import s6 from '../../public/walkthrough/v5/s6.json';
import s7 from '../../public/walkthrough/v5/s7.json';
import s9 from '../../public/walkthrough/v5/s9.json';
import s10 from '../../public/walkthrough/v5/s10.json';
import s11 from '../../public/walkthrough/v5/s11.json';

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;
export const BAR = 34;

export const sec = (s: number) => Math.round(s * FPS);

export type VoiceLine = {line: number; text: string; start: number; end: number};
export const LINES = voiceFile as VoiceLine[];
export const VOICE_SOURCE = 'walkthrough/v5/voice_azure_v5.mp3';

const findLine = (n: number): VoiceLine => {
  const line = LINES.find((l) => l.line === n);
  if (!line) throw new Error(`Voice line ${n} is missing from voice_lines_v5.json`);
  return line;
};

// The mp3 slice starts exactly where line 8 starts, so every cue below is
// derived from the json rather than from a hand-written second.
export const START = findLine(8).start;
export const TAIL = 1.0;
export const DURATION_S = findLine(14).end + TAIL - START;
export const DURATION = sec(DURATION_S);

export const at = (n: number, offset = 0) => findLine(n).start + offset - START;
export const length = (n: number) => findLine(n).end - findLine(n).start;
export const within = (n: number, p: number) => at(n, p * length(n));

export type Box = {x: number; y: number; width: number; height: number};
export type ActionLog = {t: number; kind: 'hover' | 'click'; label: string; box: Box};

const take = (log: unknown) => log as ActionLog[];
export const TAKES = {
  s6: take(s6),
  s7: take(s7),
  s9: take(s9),
  s10: take(s10),
  s11: take(s11),
};

const action = (takeName: keyof typeof TAKES, label: string): ActionLog => {
  const found = TAKES[takeName].find((a) => a.label === label);
  if (!found) throw new Error(`${label} is missing from ${takeName}.json`);
  return found;
};

// Page CSS -> take video pixels. The page viewport starts 120 CSS px below the
// screen top (menu bar + Chrome toolbar) and the recording is 2x.
export const centre = (box: Box) => ({
  x: (box.x + box.width / 2) * 2,
  y: (box.y + box.height / 2 + 120) * 2,
});

export const SHOW_PARTS = action('s6', 'show_parts');
export const MOMENT_HOVER = action('s7', 'moment');
export const MOMENT_CLICK = TAKES.s7.filter((a) => a.label === 'moment' && a.kind === 'click')[0];
export const MUTED_TOGGLE = action('s10', 'muted_toggle');
export const REPORT_TAB = action('s9', 'report_tab');
export const PLAY_BOTH = action('s11', 'play_both');

// How far into each spoken line the logged action should land. One number per
// take; every take start and every later cue falls out of these.
export const P = {
  showParts: 0.58,
  moment: 0.5,
  muted: 0.2,
  report: 0.08,
  playBoth: 0.24,
};

export const SHOW_AT = within(9, P.showParts);
export const MOMENT_AT = within(10, P.moment);
export const MUTED_AT = within(11, P.muted);
export const REPORT_AT = within(12, P.report);
export const PLAY_AT = within(13, P.playBoth);

export const MOMENT_HOVER_AT = at(10) + (MOMENT_HOVER.t - (MOMENT_CLICK.t - P.moment * length(10)));

// Take source seconds at the first frame of each take's layer.
export const TAKE6_START = SHOW_PARTS.t - SHOW_AT;
export const TAKE7_START = MOMENT_CLICK.t - P.moment * length(10);
export const TAKE10_START = MUTED_TOGGLE.t - P.muted * length(11);
export const RATE9 = 0.8;
export const TAKE9_START = REPORT_TAB.t - RATE9 * P.report * length(12);
export const TAKE11_START = PLAY_BOTH.t - P.playBoth * length(13);

export const PRE = 4 / FPS;

// Crossfade centres. Each take runs four frames past the join and the next
// starts four frames before it, so the overlap is eight frames.
export const JOIN = [at(10), at(11), at(12), at(13)].map(sec);

export type Segment = {
  take: 's6' | 's7' | 's10' | 's9' | 's11';
  from: number;
  to: number;
  sourceStart: number;
  fade: number;
  rate: number;
};

const segment = (
  takeName: Segment['take'], from: number, to: number, sourceStart: number, fade = 8, rate = 1,
): Segment => ({take: takeName, from, to, sourceStart, fade, rate});

export const SEGMENTS: Segment[] = [
  segment('s6', 0, JOIN[0] + 4, TAKE6_START),
  segment('s7', JOIN[0] - 4, JOIN[1] + 4, TAKE7_START - PRE),
  segment('s10', JOIN[1] - 4, JOIN[2] + 4, TAKE10_START - PRE),
  segment('s9', JOIN[2] - 4, JOIN[3] + 4, TAKE9_START - RATE9 * PRE, 8, RATE9),
  segment('s11', JOIN[3] - 4, DURATION, TAKE11_START - PRE, 10),
];

export const TAKE_SRC: Record<Segment['take'], string> = {
  s6: 'walkthrough/v5/s6.mp4',
  s7: 'walkthrough/v5/s7.mp4',
  s10: 'walkthrough/v5/s10.mp4',
  s9: 'walkthrough/v5/s9.mp4',
  s11: 'walkthrough/v5/s11.mp4',
};

export const SOURCE_W = 2940;
export const SOURCE_H = 1912;
export const PAGE_TOP = 240;
export const PAGE_H = SOURCE_H - PAGE_TOP;
export const STAGE = {x: 140, y: 56, w: 1640, h: 967};

export type Cam = {at: number; cx: number; cy: number; zoom: number; glide?: number};
export type Target = {cx: number; cy: number; zoom: number};

const target = (cx: number, cy: number, zoom: number): Target => ({cx, cy, zoom});
const cam = (time: number, t: Target, glide = 1.2): Cam => ({at: time, ...t, glide});

const CHART = target(1880, 850, 1.5);
const GROUPS = target(900, 1400, 1.5);
const DOT = target(1440, 880, 1.8);
const DOT2 = target(1440, 820, 1.72);
const CHART_WIDE = target(1440, 860, 1.6);
const REPORT_TAB_CAM = target(1470, 940, 1.5);
const ADVICE = target(1815, 1420, 1.6);
const COMPARE_TAB = target(470, 440, 1.55);
const PLAY_CAM = target(2640, 760, 1.55);
const RIGHT_VIDEO = target(2400, 1040, 1.55);
const LEFT_VIDEO = target(760, 1040, 1.55);
const VERDICT = target(1000, 1330, 1.6);
const VERDICT2 = target(1000, 1360, 1.65);

export const CAMS: Cam[] = [
  cam(at(8), CHART),
  cam(at(9) + 0.15, GROUPS, 1.6),
  cam(at(10) - 0.55, DOT, 1.1),
  cam(at(10) + 0.65, DOT2, 1.2),
  cam(at(11) + 0.2, CHART_WIDE, 1.2),
  cam(at(12) - 0.5, REPORT_TAB_CAM, 0.9),
  cam(at(12) + 1.1, ADVICE, 1.4),
  cam(at(13) - 0.6, COMPARE_TAB, 1.0),
  cam(at(13) + 0.45, PLAY_CAM, 0.75),
  cam(at(13) + 2.0, RIGHT_VIDEO, 1.1),
  cam(at(13) + 3.2, LEFT_VIDEO, 1.1),
  cam(at(14) + 0.1, VERDICT, 1.3),
  cam(at(14) + 2.0, VERDICT2, 1.6),
];

export type Click = {at: number; x: number; y: number; label: string};

export const CLICKS: Click[] = [
  {at: SHOW_AT, ...centre(SHOW_PARTS.box), label: 'Show 4 parts'},
  {at: MOMENT_AT, ...centre(MOMENT_CLICK.box), label: '0:08 moment'},
  {at: MUTED_AT, ...centre(MUTED_TOGGLE.box), label: 'Without sound'},
  {at: REPORT_AT, ...centre(REPORT_TAB.box), label: 'Report tab'},
  {at: PLAY_AT, ...centre(PLAY_BOTH.box), label: 'Play both'},
];

// Measured from s6.mp4: the white playhead column is at x = 889 + 44.6 * takeT.
export const PLAYHEAD = {x0: 889, slope: 44.6};
export const playheadX = (takeT: number) => PLAYHEAD.x0 + PLAYHEAD.slope * takeT;

// The chart's x-axis spans 0:00 at x=927 to 0:40 at x=2683 in the take.
export const chartSecond = (x: number) => ((x - 927) / (2683 - 927)) * 40;

export type Point = {x: number; y: number; bow?: number};
export type Key = Point & {at: number};

const key = (time: number, p: Point): Key => ({at: time, ...p});

const playheadY = (takeT: number) => {
  const second = chartSecond(playheadX(takeT));
  return 792 - 58 * Math.exp(-Math.pow((second - 10.5) / 2.1, 2));
};

// Follow the recorded playhead through the 0:08-0:13 peak, easing in from below.
export const playheadCursor = (t: number): Point => {
  const takeT = TAKE6_START + t;
  const x = playheadX(takeT) + 22;
  const y = playheadY(takeT);
  const p = Math.min(1, Math.max(0, t / 0.7));
  const eased = 1 - Math.pow(1 - p, 3);
  return {x: x + 140 * (1 - eased), y: y + 140 * (1 - eased)};
};

export const CURSOR_KEYS: Key[] = [
  key(at(9), playheadCursor(at(9))),
  key(SHOW_AT - 0.5, {x: 1442, y: 1449, bow: -30}),
  key(SHOW_AT + 0.2, {x: 1442, y: 1449}),
  key(SHOW_AT + 0.5, {x: 140, y: 1790, bow: 40}),
  key(SHOW_AT + 1.3, {x: 390, y: 1790, bow: -20}),
  key(SHOW_AT + 2.1, {x: 650, y: 1790, bow: 20}),
  key(at(10) - 0.55, {x: 900, y: 1650}),
  key(at(10) - 0.1, {x: 1550, y: 900}),
  key(at(10) + 0.4, {x: 1480, y: 860}),
  key(MOMENT_HOVER_AT, {x: 1287, y: 753, bow: -30}),
  key(MOMENT_AT, {x: 1287, y: 753}),
  key(MOMENT_AT + 0.7, {x: 1300, y: 762}),
  key(at(11) - 0.5, {x: 1480, y: 840}),
  key(at(11) - 0.1, {x: 1500, y: 850}),
  key(at(11) + 0.3, {x: 1420, y: 760}),
  key(MUTED_AT, {x: 1317, y: 619, bow: -20}),
  key(MUTED_AT + 0.6, {x: 1317, y: 619}),
  key(MUTED_AT + 1.8, {x: 1560, y: 850}),
  key(at(12) - 0.5, {x: 1700, y: 880}),
  key(at(12) - 0.1, {x: 1500, y: 900}),
  key(REPORT_AT, {x: 1151, y: 432, bow: -30}),
  key(REPORT_AT + 0.6, {x: 1151, y: 432}),
  key(REPORT_AT + 1.8, {x: 1850, y: 900, bow: 40}),
  key(at(13) - 0.8, {x: 900, y: 900}),
  key(at(13) - 0.1, {x: 427, y: 418}),
  key(at(13) + 0.3, {x: 520, y: 480}),
  key(PLAY_AT, {x: 2652, y: 543, bow: -40}),
  key(PLAY_AT + 0.5, {x: 2652, y: 543}),
  key(PLAY_AT + 1.5, {x: 2450, y: 720}),
  key(at(14) + 0.3, {x: 2200, y: 900}),
  key(DURATION_S, {x: 2300, y: 1000}),
];

export const LABELS = {
  bedrock: {from: at(12) + 1.2, to: at(12) + 6.8, text: 'Claude on Amazon Bedrock'},
  compare: {from: at(13) + 0.9, to: at(13) + 5.0, text: 'CeraVe ad, two cuts'},
  run: {from: at(8) + 0.2, to: at(9) - 0.2, text: 'Recorded run · 10:30'},
};

export const MUSIC_SOURCE = 'walkthrough/audio/solvikmusic-chill-technology-604083.mp3';
export const MUSIC_TRACK_S = 144.72;
export const MUSIC_OFFSET = Math.max(0, MUSIC_TRACK_S - DURATION_S);

export const SFX = {
  tap: 'walkthrough/sfx/tap.mp3',
  swell: 'walkthrough/sfx/swell.mp3',
};
