export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;
export const SOURCE_W = 2940;
export const SOURCE_H = 1912;
export const DURATION_S = 116;
export const DURATION = DURATION_S * FPS;
export const FADE = 12;
export const PANEL_MAX_W = 1680;
export const PANEL_MAX_H = 860;
export const TOP_CROP = 240;

export const sec = (s: number) => Math.round(s * FPS);

export type Rect = {x: number; y: number; w: number; h: number};
export type CropState = {at: number; cx: number; cy: number; zoom: number};
export type Segment = {sourceStart: number; rate: number; frames: number};
export type Ring = {sourceTime: number; x: number; y: number};
export type Shot = {
  id: string;
  src: string;
  start: number;
  base: Rect;
  states: CropState[];
  segments: Segment[];
  rings?: Ring[];
};

const seg = (sourceStart: number, rate: number, seconds: number): Segment => ({
  sourceStart,
  rate,
  frames: sec(seconds),
});

export const SHOTS: Shot[] = [
  {
    id: 's1-open', src: 'walkthrough/s1.mp4', start: 2.5,
    base: {x: 300, y: 700, w: 2380, h: 1110},
    states: [{at: 0, cx: 1490, cy: 1255, zoom: 1}, {at: 1, cx: 1540, cy: 1240, zoom: 1.1}],
    segments: [seg(0, 0.68, 12.5)],
  },
  {
    id: 'upload', src: 'walkthrough/s3_upload.mp4', start: 18.2,
    base: {x: 825, y: 760, w: 1300, h: 730},
    states: [{at: 0, cx: 1525, cy: 1130, zoom: 1}, {at: 1, cx: 1465, cy: 1250, zoom: 1.15}],
    segments: [seg(0, 2.5, 4.8)],
    rings: [{sourceTime: 10.7, x: 1157, y: 1310}],
  },
  {
    id: 'waiting', src: 'walkthrough/s5_waiting.mp4', start: 23,
    base: {x: 900, y: 420, w: 1950, h: 1050},
    states: [{at: 0, cx: 1875, cy: 945, zoom: 1}, {at: 1, cx: 1875, cy: 945, zoom: 1.03}],
    segments: [seg(3, 1, 1.2)],
  },
  {
    id: 'progress', src: 'walkthrough/s5_progress.mp4', start: 24.2,
    base: {x: 900, y: 420, w: 1950, h: 1050},
    states: [{at: 0, cx: 1875, cy: 945, zoom: 1.02}, {at: 1, cx: 1875, cy: 1020, zoom: 1.06}],
    segments: [seg(0, 1, 1), seg(1, 20, 1)],
  },
  {
    id: 'result', src: 'walkthrough/s5_result.mp4', start: 26.2,
    base: {x: 860, y: 420, w: 2050, h: 1050},
    states: [{at: 0, cx: 1890, cy: 945, zoom: 1.02}, {at: 1, cx: 1880, cy: 1020, zoom: 1.1}],
    segments: [seg(0, 1, 1.8)],
  },
  {
    id: 's6-zoom', src: 'walkthrough/s6.mp4', start: 29.2,
    base: {x: 840, y: 650, w: 2100, h: 1100},
    states: [{at: 0, cx: 1890, cy: 950, zoom: 1}, {at: 1, cx: 1890, cy: 1430, zoom: 1.03}],
    segments: [seg(0, 1, 8.8)],
  },
  {
    id: 's6-seeing', src: 'walkthrough/s6.mp4', start: 38,
    base: {x: 840, y: 1250, w: 1250, h: 650},
    states: [{at: 0, cx: 1455, cy: 1550, zoom: 1}, {at: 1, cx: 1455, cy: 1570, zoom: 1.08}],
    segments: [seg(16, 1, 5)],
    rings: [{sourceTime: 17.5, x: 1446, y: 1450}],
  },
  {
    id: 's7-moment', src: 'walkthrough/s7.mp4', start: 43,
    base: {x: 900, y: 600, w: 1900, h: 800},
    states: [{at: 0, cx: 1850, cy: 1000, zoom: 1}, {at: 1, cx: 1750, cy: 1050, zoom: 1.15}],
    segments: [seg(3, 2.25, 4)],
    rings: [{sourceTime: 4, x: 1287, y: 753}],
  },
  {
    id: 's8-brain', src: 'walkthrough/s8.mp4', start: 47,
    base: {x: 20, y: 1080, w: 800, h: 780},
    states: [{at: 0, cx: 420, cy: 1470, zoom: 1}, {at: 1, cx: 420, cy: 1480, zoom: 1.12}],
    segments: [seg(5, 3.333, 3)],
    rings: [{sourceTime: 5, x: 400, y: 1500}],
  },
  {
    id: 's9-report', src: 'walkthrough/s9.mp4', start: 50,
    base: {x: 860, y: 500, w: 2000, h: 950},
    states: [{at: 0, cx: 1850, cy: 900, zoom: 1}, {at: 1, cx: 1850, cy: 1200, zoom: 1.15}],
    segments: [seg(0, 2.8, 5)],
    rings: [{sourceTime: 9.5, x: 1010, y: 993}],
  },
  {
    id: 's10-mute', src: 'walkthrough/s10.mp4', start: 55,
    base: {x: 840, y: 480, w: 2100, h: 950},
    states: [{at: 0, cx: 1875, cy: 920, zoom: 1}, {at: 1, cx: 1850, cy: 1100, zoom: 1.15}],
    segments: [seg(2, 2.286, 3.5)],
    rings: [{sourceTime: 4, x: 1320, y: 619}],
  },
  {
    id: 's11-compare', src: 'walkthrough/s11.mp4', start: 58.5,
    base: {x: 0, y: 380, w: 2940, h: 1200},
    states: [{at: 0, cx: 1470, cy: 950, zoom: 1}, {at: 1, cx: 1470, cy: 1020, zoom: 1.08}],
    segments: [seg(1, 1, 3.5)],
    rings: [{sourceTime: 2, x: 2652, y: 558}],
  },
  {
    id: 's11b-verdict', src: 'walkthrough/s11b.mp4', start: 62,
    base: {x: 0, y: 520, w: 2940, h: 1050},
    states: [{at: 0, cx: 1470, cy: 1040, zoom: 1}, {at: 1, cx: 1470, cy: 1100, zoom: 1.1}],
    segments: [seg(4, 1, 4)],
  },
  {
    id: 's1-close', src: 'walkthrough/s1.mp4', start: 103,
    base: {x: 300, y: 700, w: 2380, h: 1110},
    states: [{at: 0, cx: 1490, cy: 1255, zoom: 1}, {at: 1, cx: 1490, cy: 1255, zoom: 1}],
    segments: [seg(0, 1, 7)],
  },
];

export type Voice = {line: number; at: number; from: number; to: number};
export const VOICE: Voice[] = [
  {line: 1, at: 2.5, from: 0, to: 4.21},
  {line: 2, at: 7.02, from: 4.37, to: 8.45},
  {line: 3, at: 11.46, from: 8.81, to: 12.51},
  {line: 4, at: 15.2, from: 13.37, to: 14.79},
  {line: 5, at: 18.2, from: 15.03, to: 18.23},
  {line: 6, at: 23, from: 18.43, to: 22.03},
  {line: 7, at: 29.2, from: 22.33, to: 30.79},
  {line: 8, at: 38, from: 31.21, to: 35.81},
  {line: 9, at: 43, from: 36.33, to: 39.63},
  {line: 10, at: 47, from: 39.87, to: 42.23},
  {line: 11, at: 50, from: 42.37, to: 46.75},
  {line: 12, at: 55, from: 47.01, to: 50.13},
  {line: 13, at: 58.5, from: 50.33, to: 54.11},
  {line: 14, at: 67.5, from: 54.53, to: 59.8},
  {line: 15, at: 72.8, from: 59.83, to: 63.63},
  {line: 16, at: 76.78, from: 63.81, to: 69.45},
  {line: 17, at: 82.9, from: 69.93, to: 74.71},
  {line: 18, at: 88.16, from: 75.19, to: 78.33},
  {line: 19, at: 91.46, from: 78.49, to: 82.43},
  {line: 20, at: 95.44, from: 82.47, to: 87.57},
  {line: 21, at: 103, from: 87.85, to: 90.83},
  {line: 22, at: 110, from: 91.35, to: 94.09},
];

export type CardPart = {text: string; hot?: boolean};
export type Card = {
  id: string;
  start: number;
  end: number;
  kind: 'chapter' | 'wordmark' | 'close';
  parts?: CardPart[];
  lead?: string;
  title?: string;
  sub?: string;
};
export const CARDS: Card[] = [
  {id: 'open', start: 0, end: 2.5, kind: 'chapter', parts: [{text: 'Watch a '}, {text: 'brain', hot: true}, {text: ' watch your video.'}]},
  {id: 'wordmark', start: 15, end: 17, kind: 'wordmark', lead: 'Introducing', title: 'NeuroLens'},
  {id: 'use', start: 17, end: 18.2, kind: 'chapter', parts: [{text: 'Use', hot: true}, {text: ' it'}]},
  {id: 'read', start: 28, end: 29.2, kind: 'chapter', parts: [{text: 'Read', hot: true}, {text: ' it'}]},
  {id: 'built', start: 66, end: 67.5, kind: 'chapter', parts: [{text: 'How it\u2019s '}, {text: 'built', hot: true}]},
  {id: 'close', start: 110, end: 116, kind: 'close', title: 'NeuroLens', sub: 'Explore the samples at the link below'},
];

export const ROWS: {label: string; mid: number}[] = [
  {label: 'open-card', mid: 1.25},
  {label: 's1-open', mid: 8.75},
  {label: 'wordmark', mid: 16},
  {label: 'use-card', mid: 17.6},
  {label: 'upload', mid: 20.6},
  {label: 'wait-progress-result', mid: 25.5},
  {label: 'read-card', mid: 28.6},
  {label: 's6-zoom', mid: 33.6},
  {label: 's6-seeing', mid: 40.5},
  {label: 's7-moment', mid: 45},
  {label: 's8-brain', mid: 48.5},
  {label: 's9-report', mid: 52.5},
  {label: 's10-mute', mid: 56.75},
  {label: 's11-compare', mid: 62.25},
  {label: 'built-card', mid: 66.75},
  {label: 'diagram', mid: 85.25},
  {label: 's1-close', mid: 106.5},
  {label: 'close-card', mid: 113},
];

export const DIAGRAM = {
  start: 67.5,
  line15: 72.8,
  line16: 76.78,
  line17: 82.9,
  line18: 88.16,
  line19: 91.46,
  line20: 95.44,
  end: 103,
};
