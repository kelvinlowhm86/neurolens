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
export const PRE = 4 / FPS;

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

// Start of a phrase inside a line, split by character position so a
// re-recorded voice moves the cue with the words.
const phraseAt = (n: number, phrase: string) => {
  const line = findLine(n);
  const index = line.text.toLowerCase().indexOf(phrase.toLowerCase());
  if (index < 0) throw new Error(`"${phrase}" is missing from voice line ${n}`);
  return line.start + (index / line.text.length) * (line.end - line.start) - START;
};

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

// Word cues for the lines whose focus changes inside the line.
export const TYPICAL_AT = phraseAt(8, 'Above the typical line');
export const TYPICAL_TO = phraseAt(8, 'ordinary viewing') + 0.55;
export const SOUND_OFF_AT = phraseAt(11, 'Turn the sound off');
export const DROP_AT = phraseAt(11, 'whole line drops');
export const SOUND_LABEL_AT = phraseAt(11, 'Most of this response');
export const ADD_AT = phraseAt(12, 'add one second');
export const OPENING_AT = phraseAt(14, 'The short cut');
export const MIDDLE_AT = phraseAt(14, 'The long cut');

// The inset clip is source 0:07-0:14, so its own t=1.0 is video 0:08.
export const INSET = {
  src: 'walkthrough/v5/swee_0807_0814.mp4',
  // Start early enough that the old group photo (clip 3.87 s, video 0:10.9)
  // is on screen before line 10 ends.
  from: at(10) + 0.2,
  to: at(11) + 1.2,
  trim: 1.0,
  rate: 1.0,
};

// How far into each spoken line the logged action should land. One number per
// take; every take start and every later cue falls out of these.
export const P = {
  moment: 0.5,
  report: 0.08,
  playBoth: 0.24,
};

export const MOMENT_AT = within(10, P.moment);
// The toggle click is timed to the words "turn the sound off", so the take is
// slowed until the click instead of landing the click early to keep the take's
// own clock.
export const MUTED_AT = SOUND_OFF_AT;
export const REPORT_AT = within(12, P.report);
export const PLAY_AT = within(13, P.playBoth);

export const MOMENT_HOVER_AT = at(10) + (MOMENT_HOVER.t - (MOMENT_CLICK.t - P.moment * length(10)));

// Take source seconds at the first frame of each take's layer.
export const TAKE6_START = SHOW_PARTS.t - SHOW_AT;
export const TAKE7_START = MOMENT_CLICK.t - P.moment * length(10);

const MUTED_FRAME = sec(MUTED_AT);
const MUTED_JOIN = sec(at(11));
export const TAKE10_RATE = MUTED_TOGGLE.t / ((MUTED_FRAME - (MUTED_JOIN - 4)) / FPS);
export const TAKE10_SPLIT_START = MUTED_TOGGLE.t - TAKE10_RATE * PRE;

// The report take has finished its scroll by 11.2 s (measured from the take),
// so 12.5 s is a static, sharp frame with the 0:08-0:13 advice row in view.
// The tab click is shown from the moving take, then one 8-frame crossfade joins
// that still. No part of the recorded scroll is replayed.
export const FREEZE9 = 12.5;
export const REPORT_CROSS = REPORT_AT + 0.45;
export const REPORT_CAM_AT = REPORT_CROSS + 0.1;
export const TAKE9A_START = REPORT_TAB.t - (REPORT_AT - (at(12) - 0.35));
// Measured from s11.mp4: the two compare videos stay black for about a second
// after the logged click while the page preloads them (the button flips from
// "Play both" to "Pause both" at 2.60 s, the click is at 1.53 s, video frames
// appear at 2.9 s). Start the take that much later so the drawn click lands a
// couple of frames before the flip; the dead wait is trimmed, not shown.
export const PRELOAD11 = 1.0;
export const TAKE11_START = PLAY_BOTH.t + PRELOAD11 - P.playBoth * length(13);

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
  still?: boolean;
};

const segment = (
  takeName: Segment['take'], from: number, to: number, sourceStart: number,
  fade = 8, rate = 1, still = false,
): Segment => ({take: takeName, from, to, sourceStart, fade, rate, still});

const REPORT_CROSS_FRAME = sec(REPORT_CROSS);

export const SEGMENTS: Segment[] = [
  segment('s6', 0, JOIN[0] + 4, TAKE6_START),
  segment('s7', JOIN[0] - 4, JOIN[1] + 4, TAKE7_START - PRE),
  segment('s10', JOIN[1] - 4, MUTED_FRAME + 4, 0, 8, TAKE10_RATE),
  segment('s10', MUTED_FRAME - 4, JOIN[2] + 4, TAKE10_SPLIT_START),
  segment('s9', JOIN[2] - 4, REPORT_CROSS_FRAME + 4, TAKE9A_START),
  segment('s9', REPORT_CROSS_FRAME - 4, JOIN[3] + 4, FREEZE9, 8, 1, true),
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

// A framing is either a zoom about the page (keeps the 1470:836 page aspect) or
// an explicit source rectangle, used when the subject is much wider or shorter
// than the window. Explicit rectangles flagged `contain` are shown whole, with
// the page's own background above and below, rather than cropped.
export type Target = {
  cx: number; cy: number;
  zoom?: number;
  w?: number; h?: number;
  contain?: boolean;
};
export type Cam = Target & {at: number; glide?: number; ease?: 'io' | 'spring'};

const target = (cx: number, cy: number, zoom: number): Target => ({cx, cy, zoom});
const frame = (cx: number, cy: number, w: number, h: number, contain = false): Target =>
  ({cx, cy, w, h, contain});
const cam = (
  time: number, t: Target, glide = 1.2, ease: Cam['ease'] = 'io',
): Cam => ({at: time, ...t, glide, ease});

// Framings are still page regions in source pixels, but every one now has the
// window's own aspect (1640 x 933): the focus section is width-filled exactly as
// before and the extra height shows the real page above and below it, so no
// shot ever has a flat letterbox band. The spotlight dims that surrounding page.
// - PAGE: the whole page viewport (window edges).
// - CARD: the chart card edge to edge, with 8 px on each side. The crop starts
//   just above the Analysis/Report/Compare labels, so "Analysis" is whole and
//   the app header ("Sign in") is out of frame; below the card it reaches the
//   start of the group section.
// - GROUPS: the whole "What the brain is doing" section, headings and all three
//   charts, width-filled; the crop ends at the bottom of the chart ink and shows
//   the bottom of the chart card above it.
// - REPORT: the report card between two gaps in its moment list, wide enough
//   for every advice line at once.
// - COMPARE_HEAD / COMPARE: line 12 opens on the Compare controls and both
//   videos so the drawn click lands on "Play both" (COMPARE_HEAD); after the
//   click the camera glides to the videos alone (COMPARE), width-filled with a
//   small margin. The head crop keeps the controls row above and the verdict
//   below; the tight crop starts just below the card heads and keeps the whole
//   verdict and overall-response chart below, so no word is sliced and there is
//   no header or page-coloured band.
// - VERDICT: the verdict card plus the overall-response chart, at a scale that
//   puts the body text at 20 px (1.3x page zoom). LEFT and RIGHT are the ends of
//   one slow pan: at LEFT the text start is in frame, at RIGHT its end is.
const PAGE_ASPECT = (STAGE.h - BAR) / STAGE.w;
const fullH = (w: number) => w * PAGE_ASPECT;
const fullFromTop = (cx: number, top: number, w: number): Target => {
  const h = fullH(w);
  return frame(cx, top + h / 2, w, h);
};
const fullToBottom = (cx: number, bottom: number, w: number): Target => {
  const h = fullH(w);
  return frame(cx, bottom - h / 2, w, h);
};

const PAGE = target(1470, 1076, 1.0);
const CARD = fullFromTop(1890, 412, 2020);
const GROUPS = fullToBottom(1890, 1856, 2040);
// The 0:08-0:13 report row, edge to edge, with the rows above and below as
// context. The Do sentence (source x 1093-2708) sits whole inside it.
const REPORT_ROW = frame(1890, 950.5, 1950, 1109);
const COMPARE_HEAD = fullFromTop(1398, 392, 2700);
const COMPARE = fullFromTop(1462, 676, 2166);
// The compare page at full width: the crop holds the whole verdict sentence
// (source x 81-2807) and the whole "Overall response" chart, so no word is cut
// at the frame edge. The chart stays width-filled at the window's bottom edge.
const COMPARE_CHART = frame(1475, 1084, 2910, 1655);

export const CAMS: Cam[] = [
  cam(at(8), CARD),
  cam(at(9) + 0.15, GROUPS, 1.0),
  cam(at(10) - 0.55, CARD, 1.0),
  cam(at(11) + 0.1, CARD, 0.9),
  cam(at(12) - 0.35, PAGE, 0.9),
  // One spring glide on the frozen report frame, settled well before "add one
  // second" (2.8 s into line 12).
  cam(REPORT_CAM_AT, REPORT_ROW, 1.3, 'spring'),
  cam(at(13) - 0.4, COMPARE_HEAD, 1.0),
  cam(at(13) + 1.35, COMPARE, 0.9),
  cam(at(14) - 0.8, COMPARE_CHART, 0.7, 'spring'),
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
  key(SEE_AT - 0.4, {x: 1140, y: 1350, bow: 20}),
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
  key(at(14) + 0.6, {x: 300, y: 1660, bow: 20}),
  key(at(14) + 2.2, {x: 850, y: 1670}),
  key(DURATION_S, {x: 950, y: 1660}),
];

export const LABELS = {
  bedrock: {from: at(12) + 1.0, to: at(13) - 0.4, text: 'Claude on Amazon Bedrock'},
  compare: {from: at(13) + 0.5, to: at(13) + 5.1, text: 'CeraVe ad, two cuts'},
  run: {from: at(8) + 0.2, to: at(9) - 0.2, text: 'Recorded run · 10:30'},
};

export type FocusRect = {x: number; y: number; w: number; h: number};

// The bright hole for each named thing, in source pixels. The surrounding page
// is dimmed by the same soft black scrims the earlier shots use.
export const FOCUS_TYPICAL = {
  from: TYPICAL_AT - 0.5, to: TYPICAL_TO, strength: 0.42,
  // The dashed typical line (source y ~916-925) and its label (y 901-927).
  rect: {x: 900, y: 878, w: 1830, h: 80},
};
export const FOCUS_PEAK = {
  from: at(10) - 0.25, to: at(11) + 0.3, strength: 0.76,
  // The plot around the peak only: the legend above and the tooltip block
  // below stay dimmed.
  rect: {x: 880, y: 665, w: 900, h: 135},
};
export const FOCUS_CHART = {
  from: at(11) + 0.15, to: SOUND_LABEL_AT + 0.15, strength: 0.5,
  // The whole plot, including the response line near the bottom (y 1214-1258).
  rect: {x: 900, y: 550, w: 1830, h: 780},
};
export const FOCUS_DROP = {
  from: DROP_AT - 0.5, to: SOUND_LABEL_AT + 0.1, strength: 0.5,
  // The dropped "without sound" line across the whole chart width.
  rect: {x: 900, y: 1155, w: 1830, h: 130},
};
export const FOCUS_SOUND_LABEL = {
  from: SOUND_LABEL_AT - 0.5, to: at(12) - 0.25, strength: 0.82,
  rect: {x: 1560, y: 582, w: 560, h: 68},
};
export const FOCUS_REPORT = {
  from: REPORT_CROSS - 0.15, to: at(13) - 0.45, strength: 0.82,
  // Only the Do advice line on the frozen frame: its label at x 932 and the
  // sentence x 1098-2709.
  rect: {x: 910, y: 862, w: 1815, h: 58},
};
export const FOCUS_COMPARE = {
  from: at(14) - 0.7, to: DURATION_S - 0.3, strength: 0.62,
  opening: {x: 55, y: 1525, w: 505, h: 365},
  middle: {x: 600, y: 1525, w: 600, h: 365},
};

export const MUSIC_SOURCE = 'walkthrough/audio/solvikmusic-chill-technology-604083.mp3';
export const MUSIC_TRACK_S = 144.72;
export const MUSIC_OFFSET = Math.max(0, MUSIC_TRACK_S - DURATION_S);

export const SFX = {
  tap: 'walkthrough/sfx/tap.mp3',
  swell: 'walkthrough/sfx/swell.mp3',
};
