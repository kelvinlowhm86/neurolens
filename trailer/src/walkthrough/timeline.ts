export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;
export const SOURCE_W = 2940;
export const SOURCE_H = 1912;
export const DURATION_S = 116;
export const DURATION = DURATION_S * FPS;
export const FADE = 12;
export const PANEL_MAX_W = 1760;
export const PANEL_MAX_H = 940;
export const TOP_CROP = 240;

export const sec = (s: number) => Math.round(s * FPS);

export type Rect = {x: number; y: number; w: number; h: number};
export type CropState = {at: number; cx: number; cy: number; zoom: number};
export type Segment = {sourceStart: number; rate: number; frames: number};
export type Shot = {
  id: string;
  src: string;
  start: number;
  base: Rect;
  states: CropState[];
  segments: Segment[];
};

export type Cam = {at: number; cx: number; cy: number; zoom: number};
export type Spot = {from: number; to: number; x: number; y: number; w: number; h: number};
export type Still = {
  id: string;
  img: string;
  w: number;
  h: number;
  start: number;
  dur: number;
  cams: Cam[];
  spots?: Spot[];
};

const seg = (sourceStart: number, rate: number, seconds: number): Segment => ({
  sourceStart,
  rate,
  frames: sec(seconds),
});

export const SHOTS: Shot[] = [
  {
    id: 'upload', src: 'walkthrough/s3_upload.mp4', start: 18.2,
    base: {x: 1060, y: 800, w: 860, h: 520},
    states: [{at: 0, cx: 1470, cy: 1060, zoom: 1}, {at: 1, cx: 1460, cy: 1080, zoom: 1.05}],
    segments: [seg(6.6, 1, 4.8)],
  },
  {
    id: 'brain', src: 'walkthrough/s8.mp4', start: 47,
    base: {x: 140, y: 1220, w: 560, h: 520},
    states: [{at: 0, cx: 420, cy: 1480, zoom: 1}, {at: 1, cx: 420, cy: 1480, zoom: 1.04}],
    segments: [seg(5, 1, 3)],
  },
];

export const MUTE_DROP = 56.2;

export const STILLS: Still[] = [
  {
    id: 'chart', img: 'walkthrough/captures/chart.png', w: 3006, h: 1335, start: 34.4, dur: 3.6,
    cams: [{at: 0, cx: .5, cy: .5, zoom: 1}, {at: 1.0, cx: .3, cy: .42, zoom: 1.6}],
    spots: [{from: 1.6, to: 3.4, x: .02, y: .47, w: .5, h: .08}],
  },
  {
    id: 'seeing', img: 'walkthrough/captures/strip_seeing.png', w: 948, h: 777, start: 38, dur: 2.4,
    cams: [{at: 0, cx: .5, cy: .5, zoom: 1}],
    spots: [{from: .6, to: 2.2, x: .68, y: .02, w: .31, h: .12}],
  },
  {
    id: 'parts', img: 'walkthrough/captures/strips_open.png', w: 3006, h: 861, start: 40.4, dur: 2.6,
    cams: [{at: 0, cx: .38, cy: .5, zoom: 1.25}],
    spots: [{from: .8, to: 2.5, x: .0, y: .18, w: .75, h: .55}],
  },
  {
    id: 'moment', img: 'walkthrough/captures/chart_moment.png', w: 3006, h: 1335, start: 43, dur: 4,
    cams: [{at: 0, cx: .5, cy: .5, zoom: 1}, {at: .8, cx: .22, cy: .45, zoom: 2.2}],
    spots: [{from: 2.1, to: 3.9, x: .06, y: .32, w: .3, h: .28}],
  },
  {
    id: 'report-head', img: 'walkthrough/captures/report_head.png', w: 2313, h: 141, start: 50, dur: 1.8,
    cams: [{at: 0, cx: .5, cy: .45, zoom: 1}, {at: .3, cx: .5, cy: .45, zoom: 1.04}],
  },
  {
    id: 'report-moment', img: 'walkthrough/captures/report_moment_1.png', w: 2313, h: 480, start: 51.8, dur: 3.2,
    cams: [{at: 0, cx: .5, cy: .5, zoom: 1}],
    spots: [{from: 1.2, to: 3.1, x: .0, y: .68, w: 1, h: .26}],
  },
  {
    id: 'compare', img: 'walkthrough/captures/compare_chart.png', w: 2451, h: 639, start: 58.5, dur: 3.6,
    cams: [{at: 0, cx: .5, cy: .5, zoom: 1}, {at: 1.4, cx: .3, cy: .55, zoom: 1.35}],
  },
  {
    id: 'verdict', img: 'walkthrough/captures/compare_verdict.png', w: 2451, h: 579, start: 62.1, dur: 3.9,
    cams: [{at: 0, cx: .5, cy: .5, zoom: 1}],
    spots: [{from: .9, to: 3.7, x: .0, y: .33, w: 1, h: .2}],
  },
];

export type Voice = {line: number; at: number; from: number; to: number};
export const VOICE: Voice[] = [
  {line: 1, at: 1.5, from: 0, to: 4.21},
  {line: 2, at: 6.02, from: 4.37, to: 8.45},
  {line: 3, at: 10.46, from: 8.81, to: 12.51},
  {line: 4, at: 15.8, from: 13.37, to: 14.79},
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
  {id: 'hook-title', start: 14.2, end: 15.6, kind: 'chapter', parts: [{text: 'Watch a '}, {text: 'brain', hot: true}, {text: ' watch your video.'}]},
  {id: 'wordmark', start: 15.6, end: 17.4, kind: 'wordmark', lead: 'Introducing', title: 'NeuroLens'},
  {id: 'use', start: 17.4, end: 18.2, kind: 'chapter', parts: [{text: 'Use', hot: true}, {text: ' it'}]},
  {id: 'read', start: 28, end: 29.2, kind: 'chapter', parts: [{text: 'Read', hot: true}, {text: ' it'}]},
  {id: 'built', start: 66, end: 67.5, kind: 'chapter', parts: [{text: 'How it\u2019s '}, {text: 'built', hot: true}]},
];

export const HOOK = {start: 0, end: 14.2, reveal: 10.46};
export const FLOW = {start: 29.2, end: 34.4};
export const STEPS = {start: 23, end: 28};
export const SPLIT = {start: 55, end: 58.5};
export const REPRISE = {start: 103, end: 110};
export const CLOSE = {start: 110, end: 116};
export const SCENES = {HOOK, FLOW, STEPS, SPLIT, REPRISE, CLOSE};

export const ROWS: {label: string; mid: number}[] = [
  {label: 'hook', mid: 7.1},
  {label: 'hook-title', mid: 14.9},
  {label: 'wordmark', mid: 16.5},
  {label: 'use-card', mid: 17.8},
  {label: 'upload', mid: 20.6},
  {label: 'steps', mid: 25.5},
  {label: 'read-card', mid: 28.6},
  {label: 'flow', mid: 31.8},
  {label: 'chart', mid: 36.2},
  {label: 'seeing', mid: 39.2},
  {label: 'parts', mid: 41.7},
  {label: 'moment', mid: 45},
  {label: 'brain', mid: 48.5},
  {label: 'report-head', mid: 50.9},
  {label: 'report-moment', mid: 53.4},
  {label: 'split', mid: 56.75},
  {label: 'compare', mid: 60.3},
  {label: 'verdict', mid: 64.05},
  {label: 'built-card', mid: 66.75},
  {label: 'diagram', mid: 85.25},
  {label: 'reprise', mid: 106.5},
  {label: 'close', mid: 113},
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

// Music edit: the track is advanced 1 s so its return lands on the wordmark (15 s),
// then a repetitive middle stretch is cut so the video ends on the track's own ending.
export const MUSIC = {
  offset: 1,        // track second played at video 0
  cutFrom: 50.3,    // track second where the cut starts (start of a quiet dip)
  cutTo: 78.0,      // track second the music resumes from (end of the next dip)
  xfade: 1,         // crossfade across the join, seconds
};
// Video second where the join happens.
export const MUSIC_JOIN = MUSIC.cutFrom - MUSIC.offset;

export type Sfx = {at: number; src: string; volume: number};

// When each diagram box appears (video seconds); each gets a soft pop.
export const DIAGRAM_APPEAR = {
  viewerA: DIAGRAM.start + 0.25,
  websiteA: DIAGRAM.start + 1.8,
  queueA: DIAGRAM.line16 + 0.2,
  gpuA: DIAGRAM.line16 + 1.15,
  creditsA: DIAGRAM.line17 + 0.1,
  reportA: DIAGRAM.line18 + 0.15,
  watchdogA: DIAGRAM.line19 + 0.1,
};
export const DIAGRAM_POPS = Object.values(DIAGRAM_APPEAR);

// Visual layers must not overlap (cards are excluded: their fades cross over neighbours).
export function checkCoverage() {
  const layers = [
    ...SHOTS.map((s) => ({
      id: s.id,
      start: s.start,
      end: s.start + s.segments.reduce((a, seg) => a + seg.frames, 0) / FPS,
    })),
    ...STILLS.map((s) => ({id: s.id, start: s.start, end: s.start + s.dur})),
    {id: 'diagram', start: DIAGRAM.start, end: DIAGRAM.end},
    ...Object.entries(SCENES).map(([id, s]) => ({id: id.toLowerCase(), start: s.start, end: s.end})),
  ];
  for (let i = 0; i < layers.length; i++) {
    for (let j = i + 1; j < layers.length; j++) {
      const a = layers[i];
      const b = layers[j];
      const overlap = Math.min(a.end, b.end) - Math.max(a.start, b.start);
      if (overlap > 0.05) {
        throw new Error(
          `Visual layers overlap by ${overlap.toFixed(2)}s: ${a.id} (${a.start}-${a.end}) and ${b.id} (${b.start}-${b.end})`,
        );
      }
    }
  }
}

checkCoverage();
