import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {
  AbsoluteFill,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {Graph} from '../../edit/Graph';
import {Cortex} from '../../scenes/brain/Cortex';
import {useSweeBrain} from '../../walkthrough/brainData';
import {SWEE} from '../../walkthrough/timeline';
import {
  CHART,
  DURATION,
  FPS,
  MUTED,
  OVERALL,
  PEAK,
  PEAK_CLICK,
  PEAK_SCORE,
  RANGE,
  SHOT,
  TO,
  TOGGLE_CLICK,
} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const easeInOut = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

const easeOut = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

const easeOutBack = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.back(1.6)),
  });

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

const timecode = (second: number) => {
  const whole = Math.max(0, Math.floor(second));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};

const INNER_W = CHART.w - 20;
const INNER_H = CHART.h - 24;
const xOf = (second: number) => 10 + (second / TO) * INNER_W;
const yOf = (value: number) => 12 + ((RANGE[1] - value) / (RANGE[1] - RANGE[0])) * INNER_H;
const ZERO_Y = yOf(0);
const PEAK_POINT = {x: CHART.x + xOf(PEAK), y: CHART.y + yOf(PEAK_SCORE)};

const CLIP = {x: 140, y: 80, w: 256, h: 455};
const BRAIN = {x: 940, y: 80, w: 780, h: 455};
const TOGGLE = {x: 480, y: 420, w: 380, h: 96};
const TOGGLE_POINT = {x: TOGGLE.x + TOGGLE.w / 2, y: TOGGLE.y + TOGGLE.h / 2};
const CURSOR_FROM = {x: PEAK_POINT.x + 320, y: PEAK_POINT.y + 150};
const CURSOR_TO_PEAK = {x: PEAK_POINT.x + 12, y: PEAK_POINT.y + 8};

const valueAt = (data: number[], second: number) => {
  const t = clamp(second, 0, data.length - 1);
  const i = Math.floor(t);
  const u = t - i;
  const next = Math.min(i + 1, data.length - 1);
  return data[i] + (data[next] - data[i]) * u;
};

const sampleCurve = (data: number[], end: number) => {
  const points: Array<[number, number]> = [];
  for (let second = 0; second < end; second += 0.2) {
    points.push([xOf(second), yOf(valueAt(data, second))]);
  }
  points.push([xOf(end), yOf(valueAt(data, end))]);
  return points;
};

const toPath = (points: Array<[number, number]>) =>
  points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');

const areaPath = (end: number) => {
  const points = sampleCurve(OVERALL, end);
  return `${toPath(points)} L${xOf(end).toFixed(1)},${ZERO_Y.toFixed(1)} L${xOf(0).toFixed(1)},${ZERO_Y.toFixed(1)} Z`;
};

const gapPath = (end: number) => {
  const top = sampleCurve(OVERALL, end);
  const bottom = sampleCurve(MUTED, end).reverse();
  const tail = bottom.map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  return `${toPath(top)} ${tail} Z`;
};

const Label: React.FC<{x: number; y: number; children: React.ReactNode; opacity?: number}> = ({x, y, children, opacity = 1}) => (
  <div style={{
    position: 'absolute', left: x, top: y, fontFamily, fontSize: 42, fontWeight: 500,
    letterSpacing: '-.01em', color: C.muted, opacity, whiteSpace: 'nowrap',
  }}>{children}</div>
);

const Cursor: React.FC<{x: number; y: number; opacity: number; pressed: boolean}> = ({x, y, opacity, pressed}) => (
  <div style={{
    position: 'absolute', left: x, top: y, opacity, pointerEvents: 'none',
    transform: `scale(${pressed ? 0.86 : 1})`, transformOrigin: 'top left',
    filter: 'drop-shadow(0 8px 14px rgba(0,0,0,.55))', zIndex: 40,
  }}>
    <svg width={46} height={58} viewBox="0 0 46 58" aria-hidden>
      <path d="M4 3 L4 45 L15 34 L24 55 L33 50 L24 30 L40 30 Z" fill="#ECEDEF" stroke="#0A0B0D" strokeWidth={3} strokeLinejoin="round" />
    </svg>
  </div>
);

const Ring: React.FC<{x: number; y: number; p: number; colour: string}> = ({x, y, p, colour}) => {
  const radius = 12 + p * 62;
  return <div style={{
    position: 'absolute', left: x - radius, top: y - radius, width: radius * 2, height: radius * 2,
    borderRadius: 999, border: `3px solid ${colour}`, opacity: (1 - p) * 0.65,
    transform: `scale(${0.55 + p * 0.5})`, pointerEvents: 'none', zIndex: 39,
  }} />;
};

const SpeakerIcon: React.FC<{muted: boolean; colour: string}> = ({muted, colour}) => (
  <svg width={46} height={46} viewBox="0 0 40 40" aria-hidden>
    <path d="M5 16 H11 L19 9 V31 L11 24 H5 Z" fill="none" stroke={colour} strokeWidth={2.6} strokeLinejoin="round" />
    {muted
      ? <line x1={24} y1={14} x2={34} y2={26} stroke={colour} strokeWidth={2.6} strokeLinecap="round" />
      : <>
          <path d="M23 15 C26 18 26 22 23 25" fill="none" stroke={colour} strokeWidth={2.4} strokeLinecap="round" />
          <path d="M27 11 C32 16 32 24 27 29" fill="none" stroke={colour} strokeWidth={2.4} strokeLinecap="round" />
        </>}
  </svg>
);

export const Result: React.FC = () => {
  const frame = useCurrentFrame();
  const data = useSweeBrain();

  const lineProgress = frame < SHOT.b.start ? 0 : easeInOut(frame, SHOT.b.start + 4, SHOT.b.start + 72);
  const mutedProgress = easeInOut(frame, SHOT.g.start + 8, SHOT.g.start + 82);
  const gap = easeOut(frame, SHOT.g.start + 16, SHOT.g.start + 84);
  const tint = 0.2 * easeOut(frame, SHOT.c.start + 14, SHOT.c.start + 58);
  const zeroOpacity = 0.34 + 0.62 * easeOut(frame, SHOT.c.start + 4, SHOT.c.start + 28);
  const zeroLabelOpacity = 0.5 + 0.5 * easeOut(frame, SHOT.c.start + 8, SHOT.c.start + 34);
  const legendOpacity = easeOut(frame, SHOT.g.start + 14, SHOT.g.start + 44);

  const markerShown = frame >= SHOT.d.start + 10;
  const markerPop = markerShown ? easeOutBack(frame, SHOT.d.start + 10, SHOT.d.start + 30) : 0;

  const brainSweep = easeInOut(frame, SHOT.b.start + 62, SHOT.b.start + 166);
  const brainSecond = frame < SHOT.b.start + 62 ? 0 : PEAK * brainSweep;
  const brainOpacity = 0.42 + 0.58 * easeOut(frame, SHOT.b.start + 56, SHOT.b.start + 112);
  const brainGlow = easeOut(frame, SHOT.b.start + 70, SHOT.b.start + 150);
  const brainProgress = frame < SHOT.b.start ? 0.12 : 0.12 + 0.24 * easeInOut(frame, SHOT.b.start, SHOT.b.end);

  const clipScale = 1 + 0.05 * easeInOut(frame, SHOT.e.start, SHOT.e.end);
  const clipSecond = frame < PEAK_CLICK ? frame / FPS : PEAK + (frame - PEAK_CLICK) / FPS;
  const jumpGlow = 1 - easeOut(frame, PEAK_CLICK, PEAK_CLICK + 20);

  const muted = frame >= TOGGLE_CLICK;
  const toggleIn = easeOut(frame, SHOT.f.start + 2, SHOT.f.start + 20);
  const togglePulse = muted ? 0 : 0.2 + 0.06 * Math.sin(frame / 8);
  const accent = muted ? C.cool : C.hot;

  const cursorOpacity = frame < SHOT.d.start + 22
    ? 0
    : frame < SHOT.g.start
      ? easeOut(frame, SHOT.d.start + 22, SHOT.d.start + 38)
      : 1 - easeOut(frame, SHOT.g.start, SHOT.g.start + 14);

  let cursorX = CURSOR_TO_PEAK.x;
  let cursorY = CURSOR_TO_PEAK.y;
  if (frame < SHOT.f.start) {
    const p = easeInOut(frame, SHOT.d.start + 30, PEAK_CLICK - 10);
    cursorX = lerp(CURSOR_FROM.x, CURSOR_TO_PEAK.x, p);
    cursorY = lerp(CURSOR_FROM.y, CURSOR_TO_PEAK.y, p);
  } else {
    const p = easeInOut(frame, SHOT.f.start + 8, TOGGLE_CLICK - 4);
    cursorX = lerp(CURSOR_TO_PEAK.x, TOGGLE_POINT.x, p);
    cursorY = lerp(CURSOR_TO_PEAK.y, TOGGLE_POINT.y, p);
  }

  const pressed = (frame >= PEAK_CLICK && frame < PEAK_CLICK + 5)
    || (frame >= TOGGLE_CLICK && frame < TOGGLE_CLICK + 5);
  const peakRing = clamp((frame - PEAK_CLICK) / 16);
  const toggleRing = clamp((frame - TOGGLE_CLICK) / 16);

  return <AbsoluteFill style={{
    background: 'radial-gradient(1150px 760px at 50% 42%, #0B0C0F 0%, #060709 64%, #000 100%)',
    fontFamily, color: C.text, overflow: 'hidden',
  }}>
    <Label x={CLIP.x} y={26}>Swee Choon · TikTok</Label>
    <Label x={BRAIN.x} y={26}>Brain · TRIBE v2</Label>

    <div style={{
      position: 'absolute', left: CLIP.x, top: CLIP.y, width: CLIP.w, height: CLIP.h,
      borderRadius: 26, overflow: 'hidden', background: '#000', boxSizing: 'border-box',
      border: '1px solid rgba(255,255,255,.13)',
      boxShadow: `0 34px 80px rgba(0,0,0,.72), 0 0 ${34 * jumpGlow}px rgba(255,138,42,${0.4 * jumpGlow})`,
      transform: `scale(${clipScale})`, transformOrigin: 'center center',
    }}>
      <Sequence from={0} durationInFrames={PEAK_CLICK}>
        <OffthreadVideo
          src={staticFile(SWEE.video)} muted trimBefore={0}
          style={{width: '100%', height: '100%', objectFit: 'cover'}}
        />
      </Sequence>
      <Sequence from={PEAK_CLICK} durationInFrames={DURATION - PEAK_CLICK}>
        <OffthreadVideo
          src={staticFile(SWEE.video)} muted trimBefore={PEAK * FPS}
          style={{width: '100%', height: '100%', objectFit: 'cover'}}
        />
      </Sequence>
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'linear-gradient(180deg, transparent 58%, rgba(0,0,0,.62) 100%)',
      }} />
      <div style={{
        position: 'absolute', left: 16, bottom: 16, padding: '5px 13px', borderRadius: 11,
        background: 'rgba(0,0,0,.66)', fontFamily, fontSize: 42, fontWeight: 500,
        color: C.text, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em',
      }}>{timecode(clipSecond)}</div>
    </div>

    <div style={{
      position: 'absolute', left: BRAIN.x, top: BRAIN.y, width: BRAIN.w, height: BRAIN.h,
      borderRadius: 26, overflow: 'hidden', background: '#0F1013', boxSizing: 'border-box',
      border: `1px solid ${C.rule}`,
      boxShadow: '0 34px 80px rgba(0,0,0,.6), inset 0 1px 0 rgba(255,255,255,.04)',
    }}>
      <div style={{
        position: 'absolute', left: -90, top: -70, width: BRAIN.w + 180, height: BRAIN.h + 140,
        background: 'radial-gradient(closest-side, rgba(255,138,42,.24), rgba(255,138,42,0) 70%)',
        opacity: brainGlow, pointerEvents: 'none',
      }} />
      {data ? <ThreeCanvas
        width={BRAIN.w} height={BRAIN.h}
        style={{position: 'absolute', left: 0, top: 0, opacity: brainOpacity}}
        camera={{fov: 28, near: 0.1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
        gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
        dpr={1}
      >
        <Cortex data={data} second={brainSecond} progress={brainProgress} />
      </ThreeCanvas> : null}
    </div>

    <Label x={CHART.x} y={556}>Overall response · every second</Label>
    <div style={{
      position: 'absolute', left: 1180, top: 552, display: 'flex', alignItems: 'center',
      gap: 38, opacity: legendOpacity, fontFamily, fontSize: 40, color: C.text,
      letterSpacing: '-.01em',
    }}>
      <span style={{display: 'flex', alignItems: 'center', gap: 14}}>
        <span style={{width: 44, height: 4, borderRadius: 2, background: C.hot}} /> With sound
      </span>
      <span style={{display: 'flex', alignItems: 'center', gap: 14}}>
        <span style={{width: 44, height: 4, borderRadius: 2, background: C.cool}} /> Without sound
      </span>
    </div>

    <div style={{position: 'absolute', left: CHART.x, top: CHART.y, width: CHART.w, height: CHART.h}}>
      <svg width={CHART.w} height={CHART.h + 58} viewBox={`0 0 ${CHART.w} ${CHART.h + 58}`}
        style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none'}}>
        <defs>
          <linearGradient id="v6-result-gap" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={C.hot} stopOpacity=".24" />
            <stop offset="1" stopColor={C.hot} stopOpacity=".05" />
          </linearGradient>
        </defs>
        {tint > 0 ? <path d={areaPath(TO)} fill={C.hot} opacity={tint} /> : null}
        {mutedProgress > 0.001 ? <path d={gapPath(TO * mutedProgress)} fill="url(#v6-result-gap)" opacity={gap} /> : null}
        <line x1={10} x2={CHART.w - 10} y1={ZERO_Y} y2={ZERO_Y} stroke={C.text} strokeWidth={2.5}
          strokeDasharray="16 14" opacity={zeroOpacity} />
        <text x={22} y={ZERO_Y + 44} fill={C.muted} fontFamily={fontFamily} fontSize={42}
          opacity={zeroOpacity * zeroLabelOpacity}>typical viewing</text>
        {[0, 10, 20, 30, 40].map((second) => (
          <text key={second} x={xOf(second)} y={CHART.h + 44} fill={C.muted}
            fontFamily={fontFamily} fontSize={42} textAnchor={second === 0 ? 'start' : second === 40 ? 'end' : 'middle'}
            opacity={0.9} style={{fontVariantNumeric: 'tabular-nums'}}>{second}s</text>
        ))}
      </svg>
      <Graph
        values={OVERALL}
        comparison={MUTED}
        from={0}
        to={TO}
        range={RANGE}
        width={CHART.w}
        height={CHART.h}
        progress={lineProgress}
        comparisonProgress={mutedProgress}
        color={C.hot}
        comparisonColor={C.cool}
        comparisonDashed={false}
        comparisonGlow={mutedProgress * 0.55}
        axes={false}
        label="swee-overall-response"
        strokeWidth={6}
        comparisonStrokeWidth={4.5}
        marker={markerShown ? {second: PEAK, label: `${timecode(PEAK)} peak`, size: 46, pop: markerPop} : undefined}
        playhead={frame >= PEAK_CLICK ? PEAK : undefined}
      />
    </div>

    <div style={{
      position: 'absolute', left: TOGGLE.x, top: TOGGLE.y, width: TOGGLE.w, height: TOGGLE.h,
      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 18,
      borderRadius: 999, boxSizing: 'border-box', fontFamily, fontSize: 44, fontWeight: 500,
      letterSpacing: '-.01em', color: C.text,
      background: 'rgba(17,18,21,.96)',
      border: `2px solid ${muted ? 'rgba(111,126,153,.62)' : 'rgba(255,138,42,.85)'}`,
      boxShadow: muted
        ? '0 24px 60px rgba(0,0,0,.6), inset 0 1px 0 rgba(255,255,255,.05)'
        : `0 24px 60px rgba(0,0,0,.6), 0 0 44px rgba(255,138,42,${togglePulse}), inset 0 1px 0 rgba(255,255,255,.07)`,
      opacity: toggleIn,
      transform: `translateY(${(1 - toggleIn) * 18}px)`,
    }}>
      <SpeakerIcon muted={muted} colour={accent} />
      {muted ? 'Muted' : 'Sound on'}
    </div>

    {frame >= PEAK_CLICK && peakRing < 1 ? <Ring x={PEAK_POINT.x} y={PEAK_POINT.y} p={peakRing} colour={C.hot} /> : null}
    {frame >= TOGGLE_CLICK && toggleRing < 1 ? <Ring x={TOGGLE_POINT.x} y={TOGGLE_POINT.y} p={toggleRing} colour={C.hot} /> : null}
    {cursorOpacity > 0 ? <Cursor x={cursorX} y={cursorY} opacity={cursorOpacity} pressed={pressed} /> : null}

    <AbsoluteFill style={{background: '#000', opacity: 1 - easeOut(frame, 0, 12), pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
