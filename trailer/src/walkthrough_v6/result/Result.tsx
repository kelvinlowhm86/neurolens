import React from 'react';
import {Sequence, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {Graph} from '../../edit/Graph';
import {
  BrainView, Cursor, PhoneClip, Ring, SpeakerIcon, Stage, clamp, easeBack, easeInOut, easeOut, lerp,
} from '../common';
import {DURATION, FPS, MUTED, OVERALL, PEAK, PEAK_CLICK, PEAK_SCORE, RANGE, SHOT, TO, TOGGLE_CLICK} from './timeline';

// Fixed, centred layout: clip and brain in a band on top, the chart under them. Between
// shots only brightness changes, except 8e: the chart and brain fade out and the clip moves to the middle, alone.
const CLIP = {x: 560, y: 36, w: 246};
const CLIP_HERO = {x: 750, y: 166, w: 420}; // 8e: centred, alone
const BRAIN = {x: 900, y: 20, size: 470};
const CHART = {x: 150, y: 545, w: 1620, h: 320};
const CHART_BIG = {y: 270, h: 440}; // 8g: the chart rises and grows once the clip and brain have left
const PAD = {l: 10, r: 10, t: 14, b: 14};
const TOGGLE = {w: 560, h: 140, cx: 960, cy: 420, parkY: 130};

const mmss = (s: number) => `0:${String(s).padStart(2, '0')}`;

type Shot = 'a' | 'b' | 'c' | 'd' | 'e' | 'f' | 'g';
const ORDER: Shot[] = ['a', 'b', 'c', 'd', 'e', 'f', 'g'];

/** A level per shot, glided into over the first 18 frames of that shot. */
const level = (f: number, values: Record<Shot, number>) => {
  let value = values.a;
  ORDER.forEach((shot, i) => {
    if (i > 0) value = lerp(value, values[shot], easeInOut(f, SHOT[shot].start - 4, SHOT[shot].start + 14));
  });
  return value;
};

const valueAt = (data: number[], second: number) => {
  const t = clamp(second, 0, data.length - 1);
  const i = Math.floor(t);
  const next = Math.min(i + 1, data.length - 1);
  return data[i] + (data[next] - data[i]) * (t - i);
};

export const Result: React.FC = () => {
  const frame = useCurrentFrame();

  const clipOpacity = level(frame, {a: 1, b: 1, c: 0.3, d: 1, e: 1, f: 0, g: 0});
  const brainOpacity = level(frame, {a: 0.5, b: 1, c: 0.3, d: 0.45, e: 0, f: 0, g: 0});
  const chartOpacity = level(frame, {a: 1, b: 1, c: 1, d: 1, e: 0, f: 0.22, g: 1});
  const clipLift = easeInOut(frame, SHOT.e.start + 8, SHOT.e.start + 44);
  const clipBox = {x: lerp(CLIP.x, CLIP_HERO.x, clipLift), y: lerp(CLIP.y, CLIP_HERO.y, clipLift), w: lerp(CLIP.w, CLIP_HERO.w, clipLift)};
  const jumpGlow = frame >= PEAK_CLICK ? 1 - easeOut(frame, PEAK_CLICK, PEAK_CLICK + 24) : 0;

  const rise = easeInOut(frame, SHOT.g.start + 2, SHOT.g.start + 34);
  const chart = {...CHART, y: lerp(CHART.y, CHART_BIG.y, rise), h: lerp(CHART.h, CHART_BIG.h, rise)};
  const innerW = chart.w - PAD.l - PAD.r;
  const innerH = chart.h - PAD.t - PAD.b;
  const xAt = (second: number) => PAD.l + (second / TO) * innerW;
  const yAt = (value: number) => PAD.t + ((RANGE[1] - value) / (RANGE[1] - RANGE[0])) * innerH;
  const zeroY = yAt(0);

  const curve = (values: number[], end: number) => {
    const points: Array<[number, number]> = [];
    for (let second = 0; second < end; second += 0.2) points.push([xAt(second), yAt(valueAt(values, second))]);
    points.push([xAt(end), yAt(valueAt(values, end))]);
    return points;
  };
  const toPath = (points: Array<[number, number]>) =>
    points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const areaPath = () => {
    const points = curve(OVERALL, TO);
    return `${toPath(points)} L${xAt(TO).toFixed(1)},${zeroY.toFixed(1)} L${xAt(0).toFixed(1)},${zeroY.toFixed(1)} Z`;
  };
  const gapPath = (end: number) => {
    const bottom = curve(MUTED, end).reverse().map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    return `${toPath(curve(OVERALL, end))} ${bottom} Z`;
  };

  const lineProgress = easeInOut(frame, SHOT.b.start + 4, SHOT.b.start + 80);
  const tint = 0.2 * easeOut(frame, SHOT.c.start + 14, SHOT.c.start + 56);
  const zeroBright = easeOut(frame, SHOT.c.start + 4, SHOT.c.start + 28);
  const zeroOpacity = 0.3 + 0.7 * zeroBright;
  const zeroLabel = zeroBright * (1 - easeOut(frame, SHOT.g.start, SHOT.g.start + 14));
  const mutedProgress = easeInOut(frame, SHOT.g.start + 10, SHOT.g.start + 86);
  const gap = easeOut(frame, SHOT.g.start + 18, SHOT.g.start + 88);
  const legendOpacity = easeOut(frame, SHOT.g.start + 10, SHOT.g.start + 40);

  const clipSecond = frame < PEAK_CLICK ? frame / FPS : PEAK + (frame - PEAK_CLICK) / FPS;
  const markerPop = easeBack(frame, SHOT.d.start + 24, SHOT.d.start + 46);
  const peak = {
    x: CHART.x + xAt(PEAK),
    y: CHART.y + PAD.t + ((RANGE[1] - PEAK_SCORE) / (RANGE[1] - RANGE[0])) * (CHART.h - PAD.t - PAD.b),
  };

  const muted = frame >= TOGGLE_CLICK;
  const toggleIn = easeOut(frame, SHOT.f.start + 14, SHOT.f.start + 34);
  const toggleScale = lerp(1, 0.62, easeInOut(frame, TOGGLE_CLICK + 14, TOGGLE_CLICK + 40));
  const toggleOpacity = toggleIn * lerp(1, 0.9, easeOut(frame, TOGGLE_CLICK + 14, TOGGLE_CLICK + 40));
  const togglePulse = muted ? 0 : 0.2 + 0.05 * Math.sin(frame / 8);

  const press = {x: TOGGLE.cx + 150, y: TOGGLE.cy + 30};
  const rest = {x: peak.x + 18, y: peak.y + 14};
  const fromPeak = {x: peak.x + 330, y: peak.y + 190};
  let cursor = rest;
  if (frame < SHOT.f.start) {
    const p = easeInOut(frame, SHOT.d.start + 40, PEAK_CLICK - 8);
    cursor = {x: lerp(fromPeak.x, rest.x, p), y: lerp(fromPeak.y, rest.y, p)};
  } else {
    const p = easeInOut(frame, SHOT.f.start + 30, TOGGLE_CLICK - 4);
    cursor = {x: lerp(rest.x, press.x, p), y: lerp(rest.y, press.y, p)};
  }
  const cursorGate = 1 - (easeOut(frame, SHOT.e.start - 4, SHOT.e.start + 8) - easeOut(frame, SHOT.f.start + 26, SHOT.f.start + 38));
  const cursorOpacity = cursorGate * Math.min(easeOut(frame, SHOT.d.start + 30, SHOT.d.start + 44), 1 - easeOut(frame, TOGGLE_CLICK + 14, TOGGLE_CLICK + 30));
  const pressed = (frame >= PEAK_CLICK && frame < PEAK_CLICK + 5) || (frame >= TOGGLE_CLICK && frame < TOGGLE_CLICK + 5);

  const accent = muted ? C.cool : C.hot;
  const label: React.CSSProperties = {fontFamily, fontSize: 44, fontWeight: 600, letterSpacing: '-.01em'};

  return (
    <Stage>
      <Sequence from={0} durationInFrames={PEAK_CLICK} layout="none">
        <PhoneClip {...clipBox} from={0} opacity={clipOpacity} time={clipSecond} />
      </Sequence>
      <Sequence from={PEAK_CLICK} durationInFrames={DURATION - PEAK_CLICK} layout="none">
        <PhoneClip {...clipBox} from={PEAK} opacity={clipOpacity} glow={jumpGlow} time={clipSecond} />
      </Sequence>
      <BrainView x={BRAIN.x} y={BRAIN.y} size={BRAIN.size} second={clipSecond} opacity={brainOpacity} progress={0.2} />

      <div style={{position: 'absolute', left: CHART.x, top: chart.y, width: CHART.w, height: chart.h, opacity: chartOpacity, zIndex: 0}}>
        <svg width={CHART.w} height={chart.h} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
          <defs>
            <clipPath id="v6r-above-zero"><rect x={0} y={0} width={CHART.w} height={zeroY} /></clipPath>
            <linearGradient id="v6r-gap" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={C.hot} stopOpacity=".26" />
              <stop offset="1" stopColor={C.hot} stopOpacity=".06" />
            </linearGradient>
          </defs>
          {tint > 0 ? <path d={areaPath()} fill={C.hot} opacity={tint} clipPath="url(#v6r-above-zero)" /> : null}
          {mutedProgress > 0.001 ? <path d={gapPath(TO * mutedProgress)} fill="url(#v6r-gap)" opacity={gap} /> : null}
        </svg>
        {lineProgress > 0.004 ? <Graph
          values={OVERALL} comparison={mutedProgress > 0.004 ? MUTED : undefined} from={0} to={TO} range={RANGE} width={CHART.w} height={chart.h}
          progress={lineProgress} comparisonProgress={mutedProgress} color={C.hot} comparisonColor={C.cool}
          comparisonDashed={false} comparisonGlow={mutedProgress * 0.4} axes={false} label="swee-overall-response"
          strokeWidth={7} comparisonStrokeWidth={6}
        /> : null}
        <svg width={CHART.w} height={chart.h + 80} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
          <line x1={PAD.l} x2={CHART.w - PAD.r} y1={zeroY} y2={zeroY} stroke={C.text} strokeWidth={zeroBright > 0.5 ? 3.5 : 2.5}
            strokeDasharray="16 14" opacity={zeroOpacity} />
          <text x={xAt(41)} y={zeroY - 18} fill={C.text} fontFamily={fontFamily} fontSize={44} fontWeight={500}
            textAnchor="end" opacity={0.92 * zeroLabel}>typical viewing</text>
          {[0, 10, 20, 30, 40].map((second) => (
            <text key={second} x={xAt(second)} y={chart.h + 54} fill={C.muted} fontFamily={fontFamily} fontSize={44}
              textAnchor={second === 0 ? 'start' : 'middle'} style={{fontVariantNumeric: 'tabular-nums'}}>{second}s</text>
          ))}
          {markerPop > 0 ? (
            <g transform={`translate(${xAt(PEAK)} ${yAt(PEAK_SCORE)}) scale(${markerPop})`}>
              <circle r={22} fill={C.hot} opacity={0.2} />
              <circle r={10} fill={C.hot} stroke={C.text} strokeWidth={3} />
              <text x={0} y={-34} fill={C.text} fontFamily={fontFamily} fontSize={44} fontWeight={600} textAnchor="start">
                Peak {mmss(PEAK)}
              </text>
            </g>
          ) : null}
          {frame >= PEAK_CLICK ? (() => {
            const hx = xAt(clamp(clipSecond, 0, TO));
            return <line x1={hx} x2={hx} y1={PAD.t} y2={chart.h - PAD.b} stroke={C.text} strokeOpacity={0.4} strokeWidth={3} />;
          })() : null}
        </svg>
      </div>

      <div style={{
        position: 'absolute', left: CHART.x, top: chart.y + chart.h + 100, width: CHART.w,
        display: 'flex', justifyContent: 'center', gap: 80, color: C.text, opacity: legendOpacity, whiteSpace: 'nowrap', ...label, fontWeight: 500,
      }}>
        <span style={{display: 'flex', alignItems: 'center', gap: 18}}>
          <span style={{width: 64, height: 6, borderRadius: 3, background: C.hot}} /> With sound
        </span>
        <span style={{display: 'flex', alignItems: 'center', gap: 18}}>
          <span style={{width: 64, height: 6, borderRadius: 3, background: C.cool}} /> Without sound
        </span>
      </div>

      <div style={{
        position: 'absolute', left: TOGGLE.cx - TOGGLE.w / 2, top: lerp(TOGGLE.cy, TOGGLE.parkY, rise) - TOGGLE.h / 2, width: TOGGLE.w, height: TOGGLE.h,
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 22, borderRadius: 999, boxSizing: 'border-box',
        fontFamily, fontSize: 56, fontWeight: 600, letterSpacing: '-.01em', color: C.text, background: 'rgba(17,18,21,.96)',
        border: `2px solid ${muted ? 'rgba(111,126,153,.62)' : 'rgba(255,138,42,.85)'}`,
        boxShadow: muted ? '0 24px 60px rgba(0,0,0,.6)' : `0 24px 60px rgba(0,0,0,.6), 0 0 44px rgba(255,138,42,${togglePulse})`,
        opacity: toggleOpacity, transform: `scale(${toggleScale})`, zIndex: 30,
      }}>
        <SpeakerIcon muted={muted} colour={accent} />
        {muted ? 'Muted' : 'Sound on'}
      </div>

      <Ring x={peak.x} y={peak.y} p={clamp((frame - PEAK_CLICK) / 18)} />
      <Ring x={press.x} y={press.y} p={clamp((frame - TOGGLE_CLICK) / 18)} />
      {cursorOpacity > 0.01 ? <Cursor x={cursor.x} y={cursor.y} opacity={cursorOpacity} pressed={pressed} /> : null}

      <div style={{position: 'absolute', inset: 0, background: '#000', opacity: 1 - easeOut(frame, 0, 12), pointerEvents: 'none', zIndex: 100}} />
    </Stage>
  );
};
