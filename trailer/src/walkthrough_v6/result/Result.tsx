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
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

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
    easing: Easing.out(Easing.back(1.5)),
  });

const timecode = (second: number) => {
  const whole = Math.max(0, Math.floor(second));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};

type Rect = {x: number; y: number; w: number; h: number};
type Stage = {
  clip: Rect;
  clipOpacity: number;
  brain: Rect;
  brainOpacity: number;
  chart: Rect;
  legendOpacity: number;
};

// One stage, four composed layouts. Every beat is one bright thing; the parts
// glide between layouts instead of cutting.
const OVERVIEW: Stage = {
  clip: {x: 160, y: 110, w: 300, h: 534},
  clipOpacity: 1,
  brain: {x: 1240, y: 110, w: 580, h: 540},
  brainOpacity: 0.9,
  chart: {x: 160, y: 782, w: 1600, h: 248},
  legendOpacity: 0,
};

const CHART_HERO: Stage = {
  clip: {x: 110, y: 120, w: 214, h: 380},
  clipOpacity: 0.5,
  brain: {x: 110, y: 540, w: 214, h: 214},
  brainOpacity: 0.55,
  chart: {x: 400, y: 300, w: 1400, h: 680},
  legendOpacity: 0,
};

const CLIP_HERO: Stage = {
  clip: {x: 770, y: 60, w: 380, h: 675},
  clipOpacity: 1,
  brain: {x: 1250, y: 170, w: 340, h: 340},
  brainOpacity: 0.38,
  chart: {x: 150, y: 790, w: 1620, h: 230},
  legendOpacity: 0,
};

const CHART_FINAL: Stage = {
  clip: {x: 110, y: 120, w: 214, h: 380},
  clipOpacity: 0.34,
  brain: {x: 110, y: 540, w: 214, h: 214},
  brainOpacity: 0.4,
  chart: {x: 400, y: 300, w: 1400, h: 600},
  legendOpacity: 1,
};

const mixRect = (a: Rect, b: Rect, t: number): Rect => ({
  x: lerp(a.x, b.x, t),
  y: lerp(a.y, b.y, t),
  w: lerp(a.w, b.w, t),
  h: lerp(a.h, b.h, t),
});

const mixStage = (a: Stage, b: Stage, t: number): Stage => ({
  clip: mixRect(a.clip, b.clip, t),
  clipOpacity: lerp(a.clipOpacity, b.clipOpacity, t),
  brain: mixRect(a.brain, b.brain, t),
  brainOpacity: lerp(a.brainOpacity, b.brainOpacity, t),
  chart: mixRect(a.chart, b.chart, t),
  legendOpacity: lerp(a.legendOpacity, b.legendOpacity, t),
});

const PAD = {l: 10, r: 10, t: 12, b: 12};

const valueAt = (data: number[], second: number) => {
  const t = clamp(second, 0, data.length - 1);
  const i = Math.floor(t);
  const u = t - i;
  const next = Math.min(i + 1, data.length - 1);
  return data[i] + (data[next] - data[i]) * u;
};

const Label: React.FC<{x: number; y: number; size?: number; opacity?: number; children: React.ReactNode}> = ({
  x, y, size = 44, opacity = 1, children,
}) => (
  <div style={{
    position: 'absolute', left: x, top: y, fontFamily, fontSize: size, fontWeight: 500,
    letterSpacing: '-.01em', color: C.muted, opacity, whiteSpace: 'nowrap', zIndex: 6,
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
  const radius = 12 + p * 66;
  return <div style={{
    position: 'absolute', left: x - radius, top: y - radius, width: radius * 2, height: radius * 2,
    borderRadius: 999, border: `3px solid ${colour}`, opacity: (1 - p) * 0.6,
    transform: `scale(${0.55 + p * 0.5})`, pointerEvents: 'none', zIndex: 39,
  }} />;
};

const SpeakerIcon: React.FC<{muted: boolean; colour: string}> = ({muted, colour}) => (
  <svg width={50} height={50} viewBox="0 0 40 40" aria-hidden>
    <path d="M5 16 H11 L19 9 V31 L11 24 H5 Z" fill="none" stroke={colour} strokeWidth={2.6} strokeLinejoin="round" />
    {muted
      ? <line x1={24} y1={14} x2={34} y2={26} stroke={colour} strokeWidth={2.6} strokeLinecap="round" />
      : <>
          <path d="M23 15 C26 18 26 22 23 25" fill="none" stroke={colour} strokeWidth={2.4} strokeLinecap="round" />
          <path d="M27 11 C32 16 32 24 27 29" fill="none" stroke={colour} strokeWidth={2.4} strokeLinecap="round" />
        </>}
  </svg>
);

const Floor: React.FC = () => (
  <>
    <div style={{
      position: 'absolute', left: '50%', top: '58%', width: 2800, height: 2800,
      transform: 'translate(-50%, -50%) rotateX(58deg) rotateZ(-45deg)',
      backgroundImage:
        'linear-gradient(rgba(139,166,194,.10) 1px, transparent 1px), linear-gradient(90deg, rgba(139,166,194,.10) 1px, transparent 1px)',
      backgroundSize: '84px 84px',
      border: '1px solid rgba(139,166,194,.08)',
      borderRadius: 80,
      maskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.86) 42%, transparent 76%)',
      WebkitMaskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.86) 42%, transparent 76%)',
      pointerEvents: 'none',
    }} />
    <div style={{
      position: 'absolute', left: '50%', top: '52%', width: 1500, height: 900,
      transform: 'translate(-50%, -50%)',
      background: 'radial-gradient(ellipse at center, rgba(66,88,118,.20), transparent 66%)',
      filter: 'blur(20px)', pointerEvents: 'none',
    }} />
    <div style={{
      position: 'absolute', left: '50%', top: '56%', width: 1360, height: 640,
      transform: 'translate(-50%, -50%) rotate(-3deg)',
      background: 'radial-gradient(ellipse at center, rgba(255,138,42,.12), transparent 66%)',
      filter: 'blur(34px)', pointerEvents: 'none',
    }} />
  </>
);

export const Result: React.FC = () => {
  const frame = useCurrentFrame();
  const data = useSweeBrain();

  // Layout phase: 0 overview, 1 chart hero, 2 clip hero, 3 chart hero final.
  const phase = easeInOut(frame, SHOT.b.start - 16, SHOT.b.start + 14)
    + easeInOut(frame, SHOT.d.start - 16, SHOT.d.start + 20)
    + easeInOut(frame, SHOT.f.start - 16, SHOT.f.start + 20);
  const stage = phase <= 1
    ? mixStage(OVERVIEW, CHART_HERO, phase)
    : phase <= 2
      ? mixStage(CHART_HERO, CLIP_HERO, phase - 1)
      : mixStage(CLIP_HERO, CHART_FINAL, Math.min(1, phase - 2));

  const {chart} = stage;
  const innerW = chart.w - PAD.l - PAD.r;
  const innerH = chart.h - PAD.t - PAD.b;
  const xAt = (second: number) => PAD.l + (second / TO) * innerW;
  const yAt = (value: number) => PAD.t + ((RANGE[1] - value) / (RANGE[1] - RANGE[0])) * innerH;
  const zeroY = yAt(0);

  const curve = (values: number[], end: number) => {
    const points: Array<[number, number]> = [];
    for (let second = 0; second < end; second += 0.2) {
      points.push([xAt(second), yAt(valueAt(values, second))]);
    }
    points.push([xAt(end), yAt(valueAt(values, end))]);
    return points;
  };
  const toPath = (points: Array<[number, number]>) =>
    points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');

  const areaPath = (end: number) => {
    const points = curve(OVERALL, end);
    return `${toPath(points)} L${xAt(end).toFixed(1)},${zeroY.toFixed(1)} L${xAt(0).toFixed(1)},${zeroY.toFixed(1)} Z`;
  };
  const gapPath = (end: number) => {
    const top = curve(OVERALL, end);
    const bottom = curve(MUTED, end).reverse();
    const tail = bottom.map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    return `${toPath(top)} ${tail} Z`;
  };

  const lineProgress = frame < SHOT.b.start ? 0 : easeInOut(frame, SHOT.b.start + 4, SHOT.b.start + 76);
  const tint = 0.2 * easeOut(frame, SHOT.c.start + 14, SHOT.c.start + 58);
  const zeroOpacity = 0.34 + 0.62 * easeOut(frame, SHOT.c.start + 4, SHOT.c.start + 28);
  const mutedProgress = easeInOut(frame, SHOT.g.start + 8, SHOT.g.start + 82);
  const gap = easeOut(frame, SHOT.g.start + 16, SHOT.g.start + 84);
  const legendOpacity = stage.legendOpacity * easeOut(frame, SHOT.g.start + 8, SHOT.g.start + 40);

  const clipSecond = frame < PEAK_CLICK ? frame / FPS : PEAK + (frame - PEAK_CLICK) / FPS;
  const liveSecond = clamp(clipSecond, 0, (data?.count ?? 1) - 1);

  // The brain starts unlit, then lights with the playhead and keeps changing second by second.
  const light = easeInOut(frame, SHOT.b.start + 64, SHOT.b.start + 152);
  const brainSecond = frame < SHOT.b.start + 64
    ? 0
    : frame < SHOT.b.start + 152
      ? lerp(0, liveSecond, light)
      : liveSecond;

  const markerShown = frame >= SHOT.d.start + 20;
  const markerPop = markerShown ? easeOutBack(frame, SHOT.d.start + 20, SHOT.d.start + 42) : 0;

  const peakPoint = {x: chart.x + xAt(PEAK), y: chart.y + yAt(PEAK_SCORE)};

  const clipScale = 1 + 0.05 * easeInOut(frame, SHOT.e.start, SHOT.e.end);
  const jumpGlow = 1 - easeOut(frame, PEAK_CLICK, PEAK_CLICK + 20);

  const muted = frame >= TOGGLE_CLICK;
  const accent = muted ? C.cool : C.hot;

  const toggleBig = {x: chart.x + chart.w / 2 - 260, y: chart.y + chart.h / 2 - 66};
  const togglePark = {x: chart.x + chart.w - 284, y: 76};
  const shrink = easeInOut(frame, TOGGLE_CLICK + 4, TOGGLE_CLICK + 32);
  const toggleLeft = lerp(toggleBig.x, togglePark.x, shrink);
  const toggleTop = lerp(toggleBig.y, togglePark.y, shrink);
  const toggleScale = lerp(1, 0.5, shrink);
  const toggleIn = easeOut(frame, SHOT.f.start + 18, SHOT.f.start + 34);
  const togglePulse = muted ? 0 : 0.18 + 0.05 * Math.sin(frame / 8);

  const peakRing = clamp((frame - PEAK_CLICK) / 18);
  const toggleRing = clamp((frame - TOGGLE_CLICK) / 18);

  const cursorIn = easeOut(frame, SHOT.d.start + 24, SHOT.d.start + 40);
  const cursorOut = 1 - easeOut(frame, SHOT.g.start, SHOT.g.start + 14);
  const cursorOpacity = Math.min(cursorIn, cursorOut);

  const restingX = peakPoint.x + 16;
  const restingY = peakPoint.y + 10;
  const pressX = toggleBig.x + 260;
  const pressY = toggleBig.y + 66;

  let cursorX = restingX;
  let cursorY = restingY;
  if (frame < SHOT.f.start) {
    const p = easeInOut(frame, SHOT.d.start + 34, PEAK_CLICK - 10);
    cursorX = lerp(peakPoint.x + 300, restingX, p);
    cursorY = lerp(peakPoint.y + 170, restingY, p);
  } else {
    const p = easeInOut(frame, SHOT.f.start + 22, TOGGLE_CLICK - 4);
    cursorX = lerp(restingX, pressX, p);
    cursorY = lerp(restingY, pressY, p);
  }

  const pressed = (frame >= PEAK_CLICK && frame < PEAK_CLICK + 5)
    || (frame >= TOGGLE_CLICK && frame < TOGGLE_CLICK + 5);

  // Let moving parts fade while they glide between layouts: nothing slides
  // across anything else, and each beat settles into one clean composition.
  const glide = Math.sin(Math.PI * clamp(phase - Math.floor(phase)));
  const brainOpacity = stage.brainOpacity * (1 - glide);
  const smoothstep = (a: number, b: number, x: number) => {
    const t = clamp((x - a) / (b - a));
    return t * t * (3 - 2 * t);
  };
  // The title lives in two fixed spots and cross-fades, so it never slides
  // across the clip or the chart while the layouts change.
  const titleOverview = 1 - smoothstep(0.02, 0.30, phase);
  const titleChart = smoothstep(0.72, 0.98, phase) * (1 - smoothstep(1.02, 1.18, phase))
    + smoothstep(2.72, 2.98, phase);
  const brainScale = (Math.min(stage.brain.w, stage.brain.h) / 640) * 1.35;
  const brainProgress = 0.16 + 0.16 * easeInOut(frame, SHOT.b.start, SHOT.g.end);

  return <AbsoluteFill style={{
    background: 'radial-gradient(1300px 900px at 50% 40%, #0B0E13 0%, #07090C 56%, #040507 100%)',
    fontFamily, color: C.text, overflow: 'hidden',
  }}>
    <Floor />

    <div style={{
      position: 'absolute',
      left: chart.x - 70, top: chart.y - 110, width: chart.w + 140, height: chart.h + 180,
      background: 'radial-gradient(ellipse at center, rgba(4,5,7,.88) 0%, rgba(4,5,7,.5) 46%, transparent 74%)',
      pointerEvents: 'none', zIndex: 1,
    }} />

    <div style={{
      position: 'absolute', left: stage.clip.x, top: stage.clip.y, width: stage.clip.w, height: stage.clip.h,
      borderRadius: 24, overflow: 'hidden', background: '#000', boxSizing: 'border-box',
      border: '1px solid rgba(255,255,255,.13)',
      boxShadow: `0 38px 90px rgba(0,0,0,.76), 0 0 ${38 * jumpGlow}px rgba(255,138,42,${0.42 * jumpGlow})`,
      opacity: stage.clipOpacity, zIndex: 12,
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
        background: 'rgba(0,0,0,.66)', fontFamily, fontSize: 44, fontWeight: 500,
        color: C.text, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em',
      }}>{timecode(clipSecond)}</div>
    </div>

    <div style={{
      position: 'absolute', left: stage.brain.x, top: stage.brain.y, width: stage.brain.w, height: stage.brain.h,
      opacity: brainOpacity, overflow: 'hidden', borderRadius: 28, zIndex: 11,
    }}>
      <div style={{
        position: 'absolute', left: '50%', top: '50%', width: stage.brain.w * 1.7, height: stage.brain.h * 1.7,
        transform: 'translate(-50%, -50%)',
        background: 'radial-gradient(closest-side, rgba(255,138,42,.22), rgba(255,138,42,0) 72%)',
        pointerEvents: 'none',
      }} />
      {data ? <ThreeCanvas
        width={640} height={640}
        style={{
          position: 'absolute', left: '50%', top: '50%',
          transform: `translate(-50%, -50%) scale(${brainScale})`, pointerEvents: 'none',
        }}
        camera={{fov: 28, near: 0.1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
        gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
        dpr={1}
      >
        <Cortex data={data} second={brainSecond} progress={brainProgress} />
      </ThreeCanvas> : null}
    </div>

    <Label x={OVERVIEW.chart.x} y={OVERVIEW.chart.y - 66} size={46} opacity={titleOverview}>Overall response · every second</Label>
    <Label x={CHART_HERO.chart.x} y={CHART_HERO.chart.y - 66} size={46} opacity={titleChart}>Overall response · every second</Label>

    <div style={{position: 'absolute', left: chart.x, top: chart.y, width: chart.w, height: chart.h, zIndex: 5}}>
      <svg width={chart.w} height={chart.h + 52} viewBox={`0 0 ${chart.w} ${chart.h + 52}`}
        style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none'}}>
        <defs>
          <clipPath id="v6-above-zero">
            <rect x={0} y={0} width={chart.w} height={zeroY} />
          </clipPath>
          <linearGradient id="v6-result-gap" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={C.hot} stopOpacity=".24" />
            <stop offset="1" stopColor={C.hot} stopOpacity=".05" />
          </linearGradient>
        </defs>
        {tint > 0 ? <path d={areaPath(TO)} fill={C.hot} opacity={tint} clipPath="url(#v6-above-zero)" /> : null}
        {mutedProgress > 0.001 ? <path d={gapPath(TO * mutedProgress)} fill="url(#v6-result-gap)" opacity={gap} /> : null}
      </svg>

      <Graph
        values={OVERALL}
        comparison={MUTED}
        from={0}
        to={TO}
        range={RANGE}
        width={chart.w}
        height={chart.h}
        progress={lineProgress}
        comparisonProgress={mutedProgress}
        color={C.hot}
        comparisonColor={C.cool}
        comparisonDashed={false}
        comparisonGlow={mutedProgress * 0.5}
        axes={false}
        label="swee-overall-response"
        strokeWidth={7}
        comparisonStrokeWidth={5}
      />

      <svg width={chart.w} height={chart.h + 52} viewBox={`0 0 ${chart.w} ${chart.h + 52}`}
        style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none'}}>
        <line x1={PAD.l} x2={chart.w - PAD.r} y1={zeroY} y2={zeroY} stroke={C.text} strokeWidth={2.5}
          strokeDasharray="16 14" opacity={zeroOpacity} />
        <text x={chart.w - PAD.r - 6} y={zeroY - 16} fill={C.muted} fontFamily={fontFamily} fontSize={40}
          textAnchor="end" opacity={0.88 * zeroOpacity}>typical viewing</text>

        {[0, 10, 20, 30, 40].map((second) => (
          <text key={second} x={xAt(second)} y={chart.h + 38} fill={C.muted}
            fontFamily={fontFamily} fontSize={40}
            textAnchor={second === 0 ? 'start' : second === 40 ? 'end' : 'middle'}
            opacity={0.9} style={{fontVariantNumeric: 'tabular-nums'}}>{second}s</text>
        ))}

        {markerShown ? (() => {
          const px = xAt(PEAK);
          const py = yAt(PEAK_SCORE);
          return <g>
            <line x1={px} x2={px} y1={PAD.t} y2={chart.h - PAD.b} stroke={C.text} strokeOpacity={0.14} strokeWidth={2} />
            <g transform={`translate(${px} ${py}) scale(${markerPop})`}>
              <circle r={18} fill={C.hot} opacity={0.18} />
              <circle r={8} fill={C.hot} stroke="#0B0C0F" strokeWidth={3} />
              <text x={26} y={-20} fill={C.text} fontFamily={fontFamily} fontSize={44} fontWeight={500}>Peak</text>
            </g>
          </g>;
        })() : null}
      </svg>
    </div>

    <div style={{
      position: 'absolute', left: chart.x, top: chart.y + chart.h + 66, width: chart.w,
      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 64,
      fontFamily, fontSize: 46, fontWeight: 500, letterSpacing: '-.01em', color: C.text,
      opacity: legendOpacity, whiteSpace: 'nowrap', zIndex: 7,
    }}>
      <span style={{display: 'flex', alignItems: 'center', gap: 18}}>
        <span style={{width: 64, height: 6, borderRadius: 3, background: C.hot}} /> With sound
      </span>
      <span style={{display: 'flex', alignItems: 'center', gap: 18}}>
        <span style={{width: 64, height: 6, borderRadius: 3, background: C.cool}} /> Without sound
      </span>
    </div>

    <div style={{
      position: 'absolute', left: toggleLeft, top: toggleTop, width: 520, height: 132,
      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 20,
      borderRadius: 999, boxSizing: 'border-box', fontFamily, fontSize: 52, fontWeight: 500,
      letterSpacing: '-.01em', color: C.text,
      background: 'rgba(17,18,21,.96)',
      border: `2px solid ${muted ? 'rgba(111,126,153,.62)' : 'rgba(255,138,42,.85)'}`,
      boxShadow: muted
        ? '0 24px 60px rgba(0,0,0,.6), inset 0 1px 0 rgba(255,255,255,.05)'
        : `0 24px 60px rgba(0,0,0,.6), 0 0 44px rgba(255,138,42,${togglePulse}), inset 0 1px 0 rgba(255,255,255,.07)`,
      opacity: toggleIn,
      transform: `scale(${toggleScale})`, transformOrigin: 'top left', zIndex: 30,
    }}>
      <SpeakerIcon muted={muted} colour={accent} />
      {muted ? 'Muted' : 'Sound on'}
    </div>

    {frame >= PEAK_CLICK && peakRing < 1 ? <Ring x={peakPoint.x} y={peakPoint.y} p={peakRing} colour={C.hot} /> : null}
    {frame >= TOGGLE_CLICK && toggleRing < 1 ? <Ring x={pressX} y={pressY} p={toggleRing} colour={C.hot} /> : null}
    {cursorOpacity > 0 ? <Cursor x={cursorX} y={cursorY} opacity={cursorOpacity} pressed={pressed} /> : null}

    <AbsoluteFill style={{background: '#000', opacity: 1 - easeOut(frame, 0, 12), pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
