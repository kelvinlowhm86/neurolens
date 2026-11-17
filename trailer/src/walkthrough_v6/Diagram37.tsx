import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {ease} from '../edit/anim';
import {
  Caption,
  Counter,
  DANGER,
  Floor,
  Gate,
  Icon,
  Link,
  Node,
  NodeState,
  Pt,
  StateKey,
  Traveller,
  pathPoint,
  stateAt,
} from './Diagram';
import {ClipArt} from './styles/shared';

const RATE = 2.6;
const HOLD = 0.5;
const FPS = 30;

const LINES: [string, string][] = [
  ['3a', 'So where does the video go?'],
  ['3b', 'Straight into storage, through a short-lived upload link. Big files never pass through the website.'],
  ['3c', 'The website just puts the job in a queue, and moves on.'],
  ['3d', "It's asynchronous. Nothing waits on the GPU, so each side can scale, or fail, on its own."],
  ['4', 'Before a GPU gets involved, a small function checks the file.'],
  ['4', 'It measures the real length, instead of trusting the browser, and confirms the price.'],
  ['4', 'A bad file never wakes a GPU.'],
  ['5a', "And the GPU only runs when there's work."],
  ['5a', 'A job arrives, and it scales up.'],
  ['5b', 'Things go quiet, and it scales back to zero.'],
  ['5c', 'The website and the database are serverless, so there’s no server to look after.'],
  ['5c', 'They grow with traffic, and cost almost nothing when idle.'],
  ['5d', "The trade-off is a few minutes' wait while the GPU starts."],
  ['5d', "Nobody needs this result in a second, so it's a trade worth making."],
  ['6', "On the GPU, Meta's TRIBE model, trained on real brain scans, watches the video twice."],
  ['6', "Once with sound, once on mute. You'll see why."],
  ['7a', "And when something goes wrong, you don't pay for it."],
  ['7a', "You're only charged once the result is saved. If the job fails, the hold goes back."],
  ['7b', "Billing is idempotent. If the same job arrives twice, it's only charged once."],
  ['7c', 'And a watchdog makes sure no GPU is left running.'],
  ['7d', 'All of this is infrastructure as code, in Terraform.'],
  ['7e', 'So the whole system can be reviewed, rebuilt, or torn down from code.'],
  ['7f', 'Every part gets least privilege. It can only do its own job.'],
];

let cursor = 0;
export const TIMING = LINES.map(([row, text]) => {
  const frames = Math.round((text.trim().split(/\s+/).length / RATE + HOLD) * FPS);
  const start = cursor;
  cursor += frames;
  return {row, text, start, end: cursor, frames};
});
export const DURATION = cursor;

const CUE = (row: string, occurrence = 0) => {
  const matches = TIMING.filter((line) => line.row === row);
  return matches[occurrence] ?? matches[0];
};

const P = {
  browser: {x: 280, y: 720},
  website: {x: 690, y: 720},
  storage: {x: 900, y: 300},
  queue: {x: 1110, y: 720},
  check: {x: 1510, y: 720},
  gpu: {x: 1970, y: 720},
  database: {x: 690, y: 1090},
  watchdog: {x: 1970, y: 300},
  dead: {x: 2380, y: 1060},
};

type Camera = {cx: number; cy: number; scale: number};
type Box = {x1: number; y1: number; x2: number; y2: number};
type FocusOptions = {primary?: Pt; width?: number; height?: number; maxScale?: number; bias?: number; lift?: number};

const camera = (cx: number, cy: number, scale: number): Camera => ({cx, cy, scale});

const nodeBox = (p: Pt): Box => ({x1: p.x - 190, y1: p.y - 84, x2: p.x + 190, y2: p.y + 200});
const propBox = (left: number, top: number, width: number, height: number): Box => ({
  x1: left,
  y1: top,
  x2: left + width,
  y2: top + height,
});
const unionBox = (...boxes: Box[]): Box => ({
  x1: Math.min(...boxes.map((b) => b.x1)),
  y1: Math.min(...boxes.map((b) => b.y1)),
  x2: Math.max(...boxes.map((b) => b.x2)),
  y2: Math.max(...boxes.map((b) => b.y2)),
});

const focus = (boxes: Box[], options: FocusOptions = {}): Camera => {
  const area = unionBox(...boxes);
  const w = Math.max(1, area.x2 - area.x1);
  const h = Math.max(1, area.y2 - area.y1);
  const scale = Math.min(
    (1920 * (options.width ?? 0.6)) / w,
    (1080 * (options.height ?? 0.76)) / h,
    options.maxScale ?? 1.48,
  );
  const bx = (area.x1 + area.x2) / 2;
  const by = (area.y1 + area.y2) / 2;
  const px = options.primary?.x ?? bx;
  const py = options.primary?.y ?? by;
  const bias = options.bias ?? 0.6;
  const lift = options.lift ?? 20;
  return camera(bx + (px - bx) * bias, by + (py - by) * 0.35 + lift / scale, scale);
};

const COUNTER = {x: P.gpu.x + 150, y: P.gpu.y - 118};
const READOUT = {left: 1180, top: 920, width: 760, height: 108};
const CLOCK = {left: 1560, top: 460, width: 660, height: 220};
const JOBS = {left: 1930, top: 880, width: 560, height: 120};

const CAM_3A = focus([nodeBox(P.browser), nodeBox(P.website)], {primary: P.website, bias: 0.55});
const CAM_3B = focus([nodeBox(P.browser), nodeBox(P.storage), nodeBox(P.website)], {primary: P.storage, bias: 0.55});
const CAM_3C = focus([nodeBox(P.website), nodeBox(P.queue)], {primary: P.queue});
const CAM_4A = focus([nodeBox(P.queue), nodeBox(P.check)], {primary: P.check});
const CAM_4B = focus(
  [nodeBox(P.queue), nodeBox(P.check), propBox(READOUT.left, READOUT.top, READOUT.width, READOUT.height)],
  {primary: P.check},
);
const CAM_4C = focus([nodeBox(P.queue), nodeBox(P.check), nodeBox(P.gpu)], {primary: P.check});
const CAM_5A = focus([nodeBox(P.check), nodeBox(P.gpu), propBox(COUNTER.x - 170, COUNTER.y - 60, 340, 120)], {
  primary: P.gpu,
});
const CAM_5C = focus([nodeBox(P.website), nodeBox(P.database)], {primary: {x: P.website.x, y: 905}});
const CAM_5D = focus(
  [
    nodeBox(P.check),
    nodeBox(P.gpu),
    propBox(CLOCK.left, CLOCK.top, CLOCK.width, CLOCK.height),
  ],
  {primary: {x: 1900, y: 600}},
);
const CAM_7A = focus([nodeBox(P.queue), nodeBox(P.check), nodeBox(P.gpu)], {primary: P.gpu});
const CAM_7B = focus([nodeBox(P.gpu), propBox(JOBS.left, JOBS.top, JOBS.width, JOBS.height)], {
  primary: {x: 2180, y: 900},
});
const CAM_7C = focus(
  [nodeBox(P.check), nodeBox(P.gpu), nodeBox(P.watchdog), nodeBox(P.dead)],
  {primary: P.gpu, bias: 0.4},
);

const FULL = camera(1330, 710, 0.74);
const CAM_6 = camera(1970, 720, 1.95);

const CAMS: {at: number; cam: Camera; move?: number}[] = [
  {at: 0, cam: CAM_3A},
  {at: CUE('3b').start, cam: CAM_3B},
  {at: CUE('3c').start, cam: CAM_3C},
  {at: CUE('4').start, cam: CAM_4A},
  {at: CUE('4', 1).start, cam: CAM_4B},
  {at: CUE('4', 2).start, cam: CAM_4C},
  {at: CUE('5a').start, cam: CAM_5A},
  {at: CUE('5c').start, cam: CAM_5C},
  {at: CUE('5d').start, cam: CAM_5D},
  {at: CUE('6').start + 56, cam: CAM_6, move: 56},
  {at: CUE('7a').start, cam: CAM_7A, move: 80},
  {at: CUE('7b').start, cam: CAM_7B, move: 36},
  {at: CUE('7c').start, cam: CAM_7C, move: 36},
  {at: CUE('7d').start, cam: FULL, move: 40},
  {at: CUE('7e').start, cam: FULL},
  {at: CUE('7f').start, cam: FULL},
];

const cameraAt = (f: number): Camera => {
  let current = CAMS[0].cam;
  for (let i = 1; i < CAMS.length; i += 1) {
    const next = CAMS[i];
    if (f >= next.at) {
      current = next.cam;
      continue;
    }
    const move = next.move ?? 26;
    if (f > next.at - move) {
      const t = interpolate(f, [next.at - move, next.at], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
        easing: (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2),
      });
      return {
        cx: current.cx + (next.cam.cx - current.cx) * t,
        cy: current.cy + (next.cam.cy - current.cy) * t,
        scale: current.scale + (next.cam.scale - current.scale) * t,
      };
    }
    return current;
  }
  return current;
};

const worldStyle = (cam: Camera): React.CSSProperties => ({
  transform: `translate(${960 - cam.cx * cam.scale}px, ${540 - cam.cy * cam.scale}px) scale(${cam.scale})`,
  transformOrigin: '0 0',
});

const nodeState = (f: number, keys: StateKey[]) => {
  const s = stateAt(f, keys);
  return {state: s.state, stateAt: s.at, prev: s.prev};
};

const glowOf = (f: number, keys: StateKey[]) => {
  const value: Record<NodeState, number> = {hidden: 0, idle: 0.2, lit: 1, dim: 0.06, off: 0};
  const s = stateAt(f, keys);
  const from = s.prev ? value[s.prev] : value[s.state];
  const t = s.prev ? interpolate(f, [s.at, s.at + 10], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;
  return from + (value[s.state] - from) * t;
};

const BROWSER: StateKey[] = [
  {at: 6, state: 'idle'},
  {at: CUE('3b').start + 6, state: 'lit'},
  {at: CUE('3b').start + 108, state: 'idle'},
  {at: CUE('3c').start + 8, state: 'dim'},
];

const WEBSITE: StateKey[] = [
  {at: 2, state: 'lit'},
  {at: CUE('3b').start + 16, state: 'idle'},
  {at: CUE('3c').start + 4, state: 'lit'},
  {at: CUE('3c').start + 74, state: 'dim'},
  {at: CUE('5c').start + 6, state: 'lit'},
  {at: CUE('5c').start + 66, state: 'idle'},
  {at: CUE('5c', 1).start + 4, state: 'lit'},
  {at: CUE('5c', 1).start + 62, state: 'dim'},
];

const STORAGE: StateKey[] = [
  {at: CUE('3b').start, state: 'idle'},
  {at: CUE('3b').start + 22, state: 'lit'},
  {at: CUE('3b').start + 112, state: 'idle'},
];

const QUEUE: StateKey[] = [
  {at: CUE('3c').start, state: 'idle'},
  {at: CUE('3c').start + 26, state: 'lit'},
  {at: CUE('3c').start + 116, state: 'idle'},
  {at: CUE('4').start + 8, state: 'lit'},
  {at: CUE('4').start + 96, state: 'idle'},
];

const CHECK: StateKey[] = [
  {at: CUE('4').start, state: 'idle'},
  {at: CUE('4').start + 34, state: 'lit'},
  {at: CUE('4', 2).start + 78, state: 'dim'},
];

const GPU: StateKey[] = [
  {at: CUE('5a').start, state: 'idle'},
  {at: CUE('5a', 1).start, state: 'lit'},
  {at: CUE('5b').start, state: 'dim'},
  {at: CUE('5d').start, state: 'idle'},
  {at: CUE('6').start, state: 'lit'},
  {at: CUE('7a', 1).start, state: 'off'},
  {at: CUE('7c').start, state: 'lit'},
  {at: CUE('7c').start + 54, state: 'off'},
];

const DATABASE: StateKey[] = [
  {at: CUE('5c').start, state: 'idle'},
  {at: CUE('5c').start + 38, state: 'lit'},
  {at: CUE('5c').start + 108, state: 'idle'},
  {at: CUE('5c', 1).start + 4, state: 'lit'},
  {at: CUE('5c', 1).start + 64, state: 'dim'},
];

const WATCHDOG: StateKey[] = [
  {at: CUE('7c').start, state: 'idle'},
  {at: CUE('7c').start + 26, state: 'lit'},
  {at: CUE('7c').start + 104, state: 'idle'},
];

const DEAD: StateKey[] = [
  {at: CUE('7c').start, state: 'idle'},
  {at: CUE('7c').start + 50, state: 'lit'},
  {at: CUE('7c').start + 112, state: 'idle'},
];

const Readout: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('4', 1).start - 8, CUE('4', 1).start + 4) *
    (1 - ease(f, CUE('5a').start - 6, CUE('5a').start + 6));
  if (opacity <= 0.001) return null;
  const length = ease(f, CUE('4', 1).start, CUE('4', 1).start + 10);
  const price = ease(f, CUE('4', 1).start + 64, CUE('4', 1).start + 76);
  return (
    <div
      style={{
        position: 'absolute',
        left: 1220,
        top: 928,
        width: 690,
        height: 96,
        display: 'flex',
        alignItems: 'center',
        gap: 38,
        padding: '0 30px',
        boxSizing: 'border-box',
        borderRadius: 24,
        background: 'rgba(13,14,17,.94)',
        border: `1.5px solid ${C.rule}`,
        boxShadow: '0 22px 54px rgba(0,0,0,.52)',
        fontFamily,
        fontSize: 50,
        fontVariantNumeric: 'tabular-nums',
        whiteSpace: 'nowrap',
        opacity,
      }}
    >
      <span style={{color: C.text, opacity: length, display: 'flex', alignItems: 'center', gap: 12}}>
        length 0:44 <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={32} /></span>
      </span>
      <span style={{color: C.text, opacity: price, display: 'flex', alignItems: 'center', gap: 12}}>
        price <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={32} /></span>
      </span>
    </div>
  );
};

const ClockDial: React.FC<{turn: number}> = ({turn}) => (
  <svg width={44} height={44} viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" style={{display: 'block'}}>
    <circle cx="32" cy="32" r="20" />
    <path d="M32 32l9 6" opacity=".55" />
    <path d="M32 32V18" transform={`rotate(${turn * 270} 32 32)`} />
  </svg>
);

const ClockNote: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('5d').start - 4, CUE('5d').start + 8) *
    (1 - ease(f, CUE('6').start - 8, CUE('6').start + 6));
  if (opacity <= 0.001) return null;
  const tick = ease(f, CUE('5d', 1).start, CUE('5d', 1).start + 12);
  const turn = ease(f, CUE('5d').start, CUE('5d').start + 54);
  return (
    <div
      style={{
        position: 'absolute',
        left: 1580,
        top: 470,
        width: 540,
        padding: '24px 28px',
        boxSizing: 'border-box',
        borderRadius: 26,
        background: 'rgba(12,14,18,.95)',
        border: `1.5px solid ${C.rule}`,
        boxShadow: '0 22px 54px rgba(0,0,0,.5)',
        fontFamily,
        fontSize: 40,
        lineHeight: 1.1,
        whiteSpace: 'nowrap',
        color: C.text,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        opacity,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: 16, color: '#FFC08A'}}>
        <ClockDial turn={turn} />
        ~few min to start
      </div>
      <div style={{display: 'flex', alignItems: 'center', gap: 16, opacity: tick, color: '#C6CCD4'}}>
        <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={40} /></span>
        not needed in a second
      </div>
    </div>
  );
};

const GpuPanel: React.FC<{f: number}> = ({f}) => {
  const opacity =
    interpolate(f, [CUE('6').start, CUE('6').start + 24, CUE('6', 1).start + 36, CUE('6', 1).start + 70], [0, 1, 1, 0], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: P.gpu.x - 360,
        top: P.gpu.y - 270,
        width: 720,
        height: 540,
        boxSizing: 'border-box',
        padding: '26px 34px 28px',
        borderRadius: 46,
        background: 'rgba(9,11,14,.97)',
        border: '2px solid rgba(255,138,42,.6)',
        boxShadow: '0 0 90px rgba(255,138,42,.24), 0 38px 90px rgba(0,0,0,.75)',
        fontFamily,
        opacity,
        transform: `scale(${0.96 + 0.04 * opacity})`,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: 18, color: '#FFC08A'}}>
        <Icon name="model" size={50} />
        <div style={{fontSize: 52, fontWeight: 600, letterSpacing: '-.03em', color: C.text}}>TRIBE (Meta)</div>
      </div>
      <div style={{display: 'flex', gap: 34, marginTop: 16}}>
        {[
          {icon: 'speaker' as const, label: 'sound on'},
          {icon: 'muted' as const, label: 'muted'},
        ].map((copy) => (
          <div key={copy.label} style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14}}>
            <ClipArt width={190} radius={18} border="1.5px solid rgba(255,255,255,.24)" />
            <div style={{display: 'flex', alignItems: 'center', gap: 12, fontSize: 36, fontWeight: 500, color: '#C6CCD4'}}>
              <span style={{color: C.hot, display: 'inline-flex'}}><Icon name={copy.icon} size={36} /></span>
              {copy.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const CreditBar: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('7a').start - 2, CUE('7a').start + 18) *
    (1 - ease(f, CUE('7b').start - 18, CUE('7b').start + 4));
  if (opacity <= 0.001) return null;
  const returned = ease(f, CUE('7a', 1).start + 20, CUE('7a', 1).start + 84);
  const barW = 470;
  return (
    <div
      style={{
        position: 'absolute',
        left: 110,
        top: 780,
        width: 530,
        height: 204,
        boxSizing: 'border-box',
        padding: '24px 30px',
        borderRadius: 28,
        background: 'rgba(10,12,16,.96)',
        border: `1.5px solid ${C.rule}`,
        boxShadow: '0 26px 64px rgba(0,0,0,.58)',
        fontFamily,
        opacity,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
        <div style={{fontSize: 40, fontWeight: 600, letterSpacing: '-.02em', color: C.text}}>credit balance</div>
        <div
          style={{
            fontSize: 40,
            fontWeight: 600,
            color: C.hot,
            opacity: 1 - returned,
            letterSpacing: '.01em',
          }}
        >
          held
        </div>
      </div>
      <div style={{position: 'relative', marginTop: 26, width: barW, height: 24, borderRadius: 12, background: '#232830', overflow: 'hidden'}}>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: 12,
            background: 'linear-gradient(90deg, rgba(255,138,42,.45), rgba(255,176,110,.9))',
            opacity: 0.72 + 0.28 * returned,
          }}
        />
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 0,
            width: 108,
            height: 24,
            background:
              'repeating-linear-gradient(135deg, rgba(9,11,14,.72) 0 7px, rgba(255,138,42,.9) 7px 13px)',
            opacity: (1 - returned) * 0.96,
          }}
        />
      </div>
      <div style={{marginTop: 14, height: 34, fontSize: 36, color: '#FFC08A', opacity: returned, display: 'flex', alignItems: 'center', gap: 10}}>
        <Icon name="tick" size={28} /> hold returned
      </div>
    </div>
  );
};

const DuplicateJob: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('7b').start + 10, CUE('7b').start + 24) *
    (1 - ease(f, CUE('7c').start - 10, CUE('7c').start + 6));
  if (opacity <= 0.001) return null;
  const first = ease(f, CUE('7b').start + 26, CUE('7b').start + 52);
  const second = ease(f, CUE('7b').start + 70, CUE('7b').start + 92);
  const bounce = ease(f, CUE('7b').start + 92, CUE('7b').start + 120);
  const firstX = 2000 + 130 * first;
  const secondX = 2000 + 140 * second - 92 * bounce;
  const secondOpacity = 1 - ease(f, CUE('7b').start + 112, CUE('7b').start + 132);
  const stamp = ease(f, CUE('7b').start + 58, CUE('7b').start + 74);
  const stampPulse = 1 + 0.06 * ease(f, CUE('7b').start + 58, CUE('7b').start + 64) * (1 - ease(f, CUE('7b').start + 64, CUE('7b').start + 80));
  const jobCard = (left: number, alpha: number) => (
    <div
      style={{
        position: 'absolute',
        left: left - 75,
        top: 901,
        width: 150,
        height: 78,
        borderRadius: 20,
        background: '#1C1E22',
        border: '1.5px solid rgba(255,158,84,.55)',
        boxShadow: '0 16px 34px rgba(0,0,0,.62), 0 0 26px rgba(255,138,42,.18)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 12,
        fontFamily,
        fontSize: 30,
        fontWeight: 500,
        color: C.text,
        opacity: alpha,
      }}
    >
      <div style={{width: 11, height: 11, borderRadius: 6, background: C.hot}} />
      job
    </div>
  );
  return (
    <div style={{position: 'absolute', inset: 0, opacity}}>
      {jobCard(firstX, 1)}
      {jobCard(secondX, secondOpacity)}
      <div
        style={{
          position: 'absolute',
          left: 2205,
          top: 888,
          width: 250,
          height: 104,
          boxSizing: 'border-box',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          border: '3px solid rgba(255,138,42,.85)',
          borderRadius: 18,
          color: '#FFC08A',
          fontFamily,
          fontSize: 44,
          fontWeight: 700,
          letterSpacing: '-.01em',
          transform: `rotate(-7deg) scale(${stampPulse})`,
          opacity: stamp,
          background: 'rgba(18,12,8,.72)',
          boxShadow: '0 0 38px rgba(255,138,42,.25)',
          textAlign: 'center',
          lineHeight: 1,
        }}
      >
        Charged
        <br />
        once
      </div>
    </div>
  );
};

const TerraformOutline: React.FC<{f: number}> = ({f}) => {
  const draw = ease(f, CUE('7d').start + 6, CUE('7d').start + 42);
  const label = ease(f, CUE('7d').start + 28, CUE('7d').start + 46);
  const revive = f >= CUE('7e').start && f <= CUE('7e').end ? 0.7 : 0;
  if (draw <= 0.001) return null;
  return (
    <>
      <svg
        width={3000}
        height={1600}
        viewBox="0 0 3000 1600"
        style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}
      >
        <rect
          x={100}
          y={200}
          width={2500}
          height={1150}
          rx={64}
          fill="none"
          stroke={revive > 0 ? 'rgba(255,178,122,.86)' : 'rgba(160,176,198,.68)'}
          strokeWidth={3}
          pathLength={1}
          strokeDasharray="1"
          strokeDashoffset={1 - draw}
          filter={revive > 0 ? 'url(#v6-outline-glow)' : undefined}
        />
        <defs>
          <filter id="v6-outline-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="7" />
          </filter>
        </defs>
      </svg>
      <div
        style={{
          position: 'absolute',
          left: 160,
          top: 156,
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '12px 24px',
          borderRadius: 999,
          background: 'rgba(10,12,16,.96)',
          border: '1.5px solid rgba(160,176,198,.45)',
          color: '#D6DCE4',
          fontFamily,
          fontSize: 54,
          fontWeight: 600,
          letterSpacing: '-.02em',
          opacity: label,
          transform: `translateY(${(1 - label) * 10}px)`,
          boxShadow: '0 18px 46px rgba(0,0,0,.55)',
        }}
      >
        <span style={{color: '#9FB0C4', display: 'inline-flex'}}><Icon name="terraform" size={44} /></span>
        Terraform
      </div>
    </>
  );
};

export const WalkthroughV6Diagram: React.FC = () => {
  const f = useCurrentFrame();
  const cam = cameraAt(f);
  const sep = ease(f, CUE('3d').start + 10, CUE('3d').start + 40);
  const dxW = -10 * sep;
  const dxQ = 10 * sep;

  const browserToWebsite = {from: {x: P.browser.x + 84, y: P.browser.y}, to: {x: P.website.x - 84 + dxW, y: P.website.y}};
  const browserToStorage = {
    from: {x: P.browser.x + 44, y: P.browser.y - 52},
    to: {x: P.storage.x - 64, y: P.storage.y + 34},
    bend: -70,
  };
  const websiteToQueue = {from: {x: P.website.x + 84 + dxW, y: P.website.y}, to: {x: P.queue.x - 84 + dxQ, y: P.queue.y}};
  const queueToCheck = {from: {x: P.queue.x + 84 + dxQ, y: P.queue.y}, to: {x: P.check.x - 84, y: P.check.y}};
  const checkToGpu = {from: {x: P.check.x + 84, y: P.check.y}, to: {x: P.gpu.x - 84, y: P.gpu.y}};
  const websiteToDatabase = {
    from: {x: P.website.x + 100 + dxW, y: P.website.y + 40},
    to: {x: P.database.x, y: P.database.y - 84},
    control: {x: 970, y: 890},
  };
  const checkToDead = {
    from: {x: P.check.x + 84, y: P.check.y + 34},
    to: {x: P.dead.x - 84, y: P.dead.y},
    control: {x: 1960, y: 1010},
  };
  const watchdogToGpu = {
    from: {x: P.watchdog.x + 60, y: P.watchdog.y + 64},
    to: {x: P.gpu.x + 60, y: P.gpu.y - 64},
    control: {x: P.gpu.x + 260, y: 510},
  };
  const forbidden = {
    from: {x: P.queue.x - 80 + dxQ, y: P.queue.y + 58},
    to: {x: P.database.x + 84, y: P.database.y},
    control: {x: 850, y: 920},
  };

  const gateAt = pathPoint(checkToGpu, 0.62);
  const gateOpacity =
    ease(f, CUE('4', 2).start - 18, CUE('4', 2).start - 6) *
    (1 - ease(f, CUE('5a').start, CUE('5a').start + 14));
  const gateClosed =
    ease(f, CUE('4', 2).start + 34, CUE('4', 2).start + 48) *
    (1 - ease(f, CUE('5a', 1).start - 20, CUE('5a', 1).start - 4));
  const gateCross =
    ease(f, CUE('4', 2).start + 56, CUE('4', 2).start + 68) *
    (1 - ease(f, CUE('5a').start, CUE('5a').start + 12));

  const running =
    f < CUE('5a', 1).start
      ? 0
      : f < CUE('5b').start
        ? ease(f, CUE('5a', 1).start + 16, CUE('5a', 1).start + 42)
        : 1 - ease(f, CUE('5b').start + 18, CUE('5b').start + 54);
  const counterOpacity =
    ease(f, CUE('5a').start - 4, CUE('5a').start + 8) *
    (1 - ease(f, CUE('5c').start - 10, CUE('5c').start + 6));

  const sceneOpacity = interpolate(
    f,
    [CUE('7e').start, CUE('7e').start + 34, CUE('7e').start + 60, CUE('7e').start + 122],
    [1, 0.06, 0.06, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  const gpuFail =
    ease(f, CUE('7a').start + 6, CUE('7a').start + 18) *
    (1 - ease(f, CUE('7b').start - 10, CUE('7b').start + 10));

  const keyBase = CUE('7f').start;

  const pool = (p: Pt, keys: StateKey[]) => ({
    x: p.x,
    y: p.y + 66,
    rx: 200,
    ry: 88,
    opacity: 0.16 + 0.58 * glowOf(f, keys),
    color: 'rgba(255,138,42,.26)',
  });

  return (
    <AbsoluteFill style={{background: 'radial-gradient(1300px 900px at 50% 42%, #0B0E13 0%, #07090C 58%, #040507 100%)'}}>
      <AbsoluteFill style={worldStyle(cam)}>
        <div style={{position: 'absolute', inset: 0, opacity: sceneOpacity}}>
          <Floor
            pools={[
              pool(P.browser, BROWSER),
              pool(P.website, WEBSITE),
              pool(P.storage, STORAGE),
              pool(P.queue, QUEUE),
              pool(P.check, CHECK),
              pool(P.gpu, GPU),
              pool(P.database, DATABASE),
              pool(P.watchdog, WATCHDOG),
              pool(P.dead, DEAD),
            ]}
          />

          <svg width={3000} height={1600} viewBox="0 0 3000 1600" style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
            <defs>
              <filter id="v6-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="8" />
              </filter>
              <filter id="v6-floor-blur" x="-70%" y="-70%" width="240%" height="240%">
                <feGaussianBlur stdDeviation="20" />
              </filter>
            </defs>

            <Link path={browserToWebsite} appear={10} draw={18} width={1.8} colour="rgba(150,166,188,.5)" />
            <Link
              path={browserToStorage}
              appear={CUE('3b').start - 4}
              draw={18}
              arrow
              hot={ease(f, CUE('3b').start + 6, CUE('3b').start + 22) * (1 - ease(f, CUE('3b').start + 96, CUE('3b').start + 126))}
            />
            <Link
              path={websiteToQueue}
              appear={CUE('3c').start - 4}
              draw={18}
              arrow
              dashed={f >= CUE('3d').start + 4}
              dashedAt={CUE('3d').start + 4}
              gap={28 * sep}
            />
            <Link path={queueToCheck} appear={CUE('4').start - 4} draw={18} arrow />
            <Link path={checkToGpu} appear={CUE('4', 2).start - 30} draw={18} arrow />
            <Link path={websiteToDatabase} appear={CUE('5c').start - 4} draw={18} arrow />
            <Link path={checkToDead} appear={CUE('7c').start - 2} draw={20} arrow />
            <Link path={watchdogToGpu} appear={CUE('7c').start + 18} draw={20} arrow />
            <Link
              path={forbidden}
              appear={CUE('7f').start + 6}
              draw={20}
              colour="rgba(160,176,198,.5)"
              blocked={ease(f, CUE('7f').start + 34, CUE('7f').start + 50)}
              arrow
            />

            {gateOpacity > 0.001 ? (
              <g opacity={gateOpacity}>
                <Gate x={gateAt.x} y={gateAt.y} closed={gateClosed} cross={gateCross} />
              </g>
            ) : null}

            <Traveller
              path={browserToStorage}
              start={CUE('3b').start + 8}
              end={CUE('3b').start + 102}
              kind="clip"
              subLabel="0:44"
              stopAt={0.75}
            />
            <Traveller
              path={{from: {x: P.queue.x, y: P.queue.y - 210}, to: {x: P.queue.x, y: P.queue.y - 52}}}
              start={CUE('3c').start + 18}
              end={CUE('3c').start + 44}
              kind="job"
              linger
              outAt={CUE('3c').start + 104}
            />
            <Traveller path={queueToCheck} start={CUE('4').start + 10} end={CUE('4').start + 50} kind="dot" />
            <Traveller
              path={checkToGpu}
              start={CUE('4', 2).start - 8}
              end={CUE('4', 2).start + 70}
              kind="dot"
              colour={DANGER}
              stopAt={0.55}
              linger
              outAt={CUE('5a').start}
            />
            <Traveller path={checkToGpu} start={CUE('5a', 1).start - 4} end={CUE('5a', 1).start + 48} kind="dot" />
            <Traveller
              path={checkToDead}
              start={CUE('7c').start + 46}
              end={CUE('7c').start + 96}
              kind="dot"
              colour={DANGER}
              linger
              outAt={CUE('7d').start - 6}
            />

            {gpuFail > 0.001 ? (
              <g opacity={gpuFail}>
                <circle cx={P.gpu.x + 46} cy={P.gpu.y - 46} r={31} fill="#0B0C0F" opacity={0.92} stroke={DANGER} strokeWidth={3} />
                <g stroke={DANGER} strokeWidth={6} strokeLinecap="round">
                  <path d={`M ${P.gpu.x + 34} ${P.gpu.y - 58} L ${P.gpu.x + 58} ${P.gpu.y - 34}`} />
                  <path d={`M ${P.gpu.x + 34} ${P.gpu.y - 34} L ${P.gpu.x + 58} ${P.gpu.y - 58}`} />
                </g>
              </g>
            ) : null}
          </svg>

          <Node
            x={P.browser.x}
            y={P.browser.y}
            label="Browser"
            service="the file"
            icon="browser"
            {...nodeState(f, BROWSER)}
            appear={6}
            keyAt={keyBase}
          />
          <Node
            x={P.website.x + dxW}
            y={P.website.y}
            label="Website"
            service="Lambda"
            icon="website"
            {...nodeState(f, WEBSITE)}
            appear={2}
            keyAt={keyBase + 7}
          />
          <Node
            x={P.storage.x}
            y={P.storage.y}
            label="Storage"
            service="S3"
            icon="storage"
            {...nodeState(f, STORAGE)}
            appear={CUE('3b').start}
            keyAt={keyBase + 14}
          />
          <Node
            x={P.queue.x + dxQ}
            y={P.queue.y}
            label="Queue"
            service="SQS"
            icon="queue"
            {...nodeState(f, QUEUE)}
            appear={CUE('3c').start}
            keyAt={keyBase + 21}
          />
          <Node
            x={P.check.x}
            y={P.check.y}
            label="Check"
            service="Lambda"
            icon="check"
            {...nodeState(f, CHECK)}
            appear={CUE('4').start}
            keyAt={keyBase + 28}
          />
          <Node
            x={P.gpu.x}
            y={P.gpu.y}
            label="GPU"
            service="SageMaker"
            icon="gpu"
            {...nodeState(f, GPU)}
            appear={CUE('5a').start}
            keyAt={keyBase + 35}
            keySide="left"
          />
          <Node
            x={P.database.x}
            y={P.database.y}
            label="Database"
            service="Aurora Serverless"
            icon="database"
            {...nodeState(f, DATABASE)}
            appear={CUE('5c').start}
            keyAt={keyBase + 42}
          />
          <Node
            x={P.watchdog.x}
            y={P.watchdog.y}
            label="Watchdog"
            service="Lambda"
            icon="watchdog"
            {...nodeState(f, WATCHDOG)}
            appear={CUE('7c').start}
            keyAt={keyBase + 49}
          />
          <Node
            x={P.dead.x}
            y={P.dead.y}
            label="Dead-letter"
            service="SQS"
            icon="deadLetter"
            {...nodeState(f, DEAD)}
            appear={CUE('7c').start}
            keyAt={keyBase + 56}
          />

          <Readout f={f} />
          <ClockNote f={f} />
          <Counter x={P.gpu.x + 150} y={P.gpu.y - 118} value={running} opacity={counterOpacity} />
          <GpuPanel f={f} />
          <DuplicateJob f={f} />
        </div>

        <TerraformOutline f={f} />
      </AbsoluteFill>

      <Caption text="asynchronous" from={CUE('3d').start + 8} to={CUE('4').start - 10} hot="asynchronous" />
      <Caption text="scale to zero" from={CUE('5b').start + 4} to={CUE('5c').start - 8} hot="zero" />
      <Caption text="serverless" from={CUE('5c').start + 6} to={CUE('5d').start - 10} hot="serverless" />
      <Caption text="idempotent" from={CUE('7b').start + 6} to={CUE('7c').start - 10} hot="idempotent" />
      <Caption text="infrastructure as code" from={CUE('7d').start + 6} to={CUE('7e').start - 6} hot="code" />
      <Caption text="least privilege" from={CUE('7f').start + 8} to={CUE('7f').end + 4} hot="privilege" />
      <CreditBar f={f} />
    </AbsoluteFill>
  );
};
