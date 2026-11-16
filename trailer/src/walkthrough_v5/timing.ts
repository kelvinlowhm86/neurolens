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

// The three group words are spoken late in line 9 ("what you see, what you hear,
// what you understand"). Split the line by character position, so a re-recorded
// voice moves the sweep with it.
const GROUPS_LINE = findLine(9);
const wordAt = (word: string) => {
  const match = new RegExp(`\\b${word}\\b`, 'i').exec(GROUPS_LINE.text);
  if (!match) throw new Error(`\"${word}\" is missing from voice line 9`);
  return GROUPS_LINE.start + (match.index / GROUPS_LINE.text.length) * (GROUPS_LINE.end - GROUPS_LINE.start);
};

export const SEE_AT = wordAt('see') - START;
export const HEAR_AT = wordAt('hear') - START;
export const UNDERSTAND_AT = wordAt('understand') - START;

// "Show 4 parts" stays after the sweep, still inside line 9.
export const SHOW_AT = Math.min(UNDERSTAND_AT + 0.5, at(9) + length(9) - 0.18);

// How far into each spoken line the logged action should land. One number per
// take; every take start and every later cue falls out of these.
export const P = {
  moment: 0.5,
  muted: 0.2,
  report: 0.08,
  playBoth: 0.24,
};

export const MOMENT_AT = within(10, P.moment);
export const MUTED_AT = within(11, P.muted);
export const REPORT_AT = within(12, P.report);
export const PLAY_AT = within(13, P.playBoth);

export const MOMENT_HOVER_AT = at(10) + (MOMENT_HOVER.t - (MOMENT_CLICK.t - P.moment * length(10)));

// Take source seconds at the first frame of each take's layer.
export const TAKE6_START = SHOW_PARTS.t - SHOW_AT;
export const TAKE7_START = MOMENT_CLICK.t - P.moment * length(10);
export const TAKE10_START = MUTED_TOGGLE.t - P.muted * length(11);
// The report take scrolls its own content for ten seconds. Run it faster so the
// 0:08-0:13 advice is settled on screen inside line 12.
export const RATE9 = 1.4;
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

// Framings are page regions, in source pixels, chosen so every edge lands on a
// panel boundary or in the gap between panels:
// - PAGE: the whole page viewport (window edges).
// - CARD: the main chart card, from its chart content to its readout, bottom in
//   the gap between the card and the strips header. 978 CSS px wide; that is
//   the tightest crop that keeps the whole plot and readout.
// - REPORT: the report card between two gaps in its moment list, wide enough
//   for every advice line at once.
const PAGE = target(1470, 1076, 1.0);
const CARD = target(1908, 796, 1470 / 978);
const REPORT = target(1894, 955, 1470 / 968);

export const CAMS: Cam[] = [
  cam(at(8), CARD),
  cam(at(9) + 0.15, PAGE, 1.0),
  cam(at(10) - 0.55, CARD, 1.0),
  cam(at(11) + 0.1, CARD, 0.9),
  cam(at(12) - 0.35, PAGE, 0.9),
  cam(at(12) + 6.8, REPORT, 0.8),
  cam(at(13) + 0.3, PAGE, 0.9),
  cam(at(14) + 0.2, PAGE, 0.8),
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
  key(at(9) + 0.2, playheadCursor(at(9) + 0.2)),
  key(SEE_AT - 0.4, {x: 1140, y: 1260, bow: 20}),
  key(SEE_AT, {x: 1000, y: 1452}),
  key(HEAR_AT, {x: 1680, y: 1452}),
  key(UNDERSTAND_AT, {x: 2490, y: 1452}),
  key(SHOW_AT, {x: 1442, y: 1449, bow: -70}),
  key(SHOW_AT + 0.5, {x: 1442, y: 1449}),
  key(at(10) + 0.05, {x: 1450, y: 1260, bow: 40}),
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
  key(PLAY_AT - 0.3, {x: 2500, y: 560, bow: 20}),
  key(PLAY_AT, {x: 2652, y: 543}),
  key(PLAY_AT + 0.5, {x: 2652, y: 543}),
  key(PLAY_AT + 1.5, {x: 2300, y: 900, bow: -30}),
  key(at(14) + 0.6, {x: 1900, y: 1240, bow: 40}),
  key(at(14) + 2.2, {x: 1800, y: 1320}),
  key(DURATION_S, {x: 1980, y: 1380}),
];

export const LABELS = {
  bedrock: {from: at(12) + 1.0, to: at(13) - 0.4, text: 'Claude on Amazon Bedrock'},
  compare: {from: at(13) + 0.5, to: at(13) + 5.1, text: 'CeraVe ad, two cuts'},
  run: {from: at(8) + 0.2, to: at(9) - 0.2, text: 'Recorded run · 10:30'},
};

export const MUSIC_SOURCE = 'walkthrough/audio/solvikmusic-chill-technology-604083.mp3';
export const MUSIC_TRACK_S = 144.72;
export const MUSIC_OFFSET = Math.max(0, MUSIC_TRACK_S - DURATION_S);

export const SFX = {
  tap: 'walkthrough/sfx/tap.mp3',
  swell: 'walkthrough/sfx/swell.mp3',
};
