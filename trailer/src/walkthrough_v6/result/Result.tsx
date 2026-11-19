import React from 'react';
import {Sequence, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {BrainView, Cursor, PhoneClip, Ring, Stage, clamp, easeBack, easeInOut, easeOut, lerp} from '../common';
import {CURSOR_IN, DURATION, FPS, MUTED, OVERALL, PEAK, PEAK_CLICK, PEAK_MARK, PEAK_SCORE, RANGE, SHOT, TO} from './timeline';
import {Chart} from './Chart';

// Fixed, centred layout: clip and brain in a band on top, the chart under them. Between
// shots only brightness changes.
const CLIP = {x: 560, y: 36, w: 246};
const BRAIN = {x: 900, y: 20, size: 470};
const CHART = {x: 150, y: 545, w: 1620, h: 320};
const PAD = {l: 10, r: 10, t: 14, b: 14};
const MUTED_INK = '#6F7E99';

const mmss = (s: number) => `0:${String(s).padStart(2, '0')}`;

type Shot = 'a' | 'b' | 'c';
const ORDER: Shot[] = ['a', 'b', 'c'];

/** A level per shot, glided into over the first 18 frames of that shot. */
const level = (f: number, values: Record<Shot, number>) => {
  let value = values.a;
  ORDER.forEach((shot, i) => {
    if (i > 0) value = lerp(value, values[shot], easeInOut(f, SHOT[shot].start - 4, SHOT[shot].start + 14));
  });
  return value;
};

export const Result: React.FC = () => {
  const frame = useCurrentFrame();

  const brainOpacity = level(frame, {a: 0.5, b: 1, c: 0.45});
  const jumpGlow = frame >= PEAK_CLICK ? 1 - easeOut(frame, PEAK_CLICK, PEAK_CLICK + 24) : 0;

  const innerW = CHART.w - PAD.l - PAD.r;
  const innerH = CHART.h - PAD.t - PAD.b;
  const xAt = (second: number) => PAD.l + (second / TO) * innerW;
  const yAt = (value: number) => PAD.t + ((RANGE[1] - value) / (RANGE[1] - RANGE[0])) * innerH;

  const zeroLabel = easeOut(frame, SHOT.b.start + 60, SHOT.b.start + 84);
  const lineProgress = easeInOut(frame, SHOT.b.start + 4, SHOT.b.start + 80);
  // The muted series draws in during 8.2, after the main line, and stays for the peak click in 8.3.
  const mutedProgress = easeInOut(frame, SHOT.b.start + 18, SHOT.b.start + 100);
  const legendOpacity = easeOut(frame, SHOT.b.start + 24, SHOT.b.start + 44);

  const clipSecond = frame < PEAK_CLICK ? frame / FPS : PEAK + (frame - PEAK_CLICK) / FPS;
  const markerPop = easeBack(frame, PEAK_MARK, PEAK_MARK + 22);
  const peak = {
    x: CHART.x + xAt(PEAK),
    y: CHART.y + PAD.t + ((RANGE[1] - PEAK_SCORE) / (RANGE[1] - RANGE[0])) * (CHART.h - PAD.t - PAD.b),
  };

  const rest = {x: peak.x + 18, y: peak.y + 14};
  const fromPeak = {x: peak.x + 330, y: peak.y + 190};
  const p = easeInOut(frame, CURSOR_IN, PEAK_CLICK - 8);
  const cursor = {x: lerp(fromPeak.x, rest.x, p), y: lerp(fromPeak.y, rest.y, p)};
  const cursorOpacity = easeOut(frame, CURSOR_IN - 10, CURSOR_IN + 4) * (1 - easeOut(frame, PEAK_CLICK + 14, PEAK_CLICK + 30));
  const pressed = frame >= PEAK_CLICK && frame < PEAK_CLICK + 5;

  return (
    <Stage>
      <Sequence from={0} durationInFrames={PEAK_CLICK} layout="none">
        <PhoneClip {...CLIP} from={0} time={clipSecond} />
      </Sequence>
      <Sequence from={PEAK_CLICK} durationInFrames={DURATION - PEAK_CLICK} layout="none">
        <PhoneClip {...CLIP} from={PEAK} glow={jumpGlow} time={clipSecond} />
      </Sequence>
      <BrainView x={BRAIN.x} y={BRAIN.y} size={BRAIN.size} second={clipSecond} opacity={brainOpacity} progress={0.2} />

      <Chart
        x={CHART.x} y={CHART.y} w={CHART.w} h={CHART.h}
        values={OVERALL} to={TO} range={RANGE} progress={lineProgress}
        comparison={MUTED} comparisonDashed comparisonColour={MUTED_INK} comparisonProgress={mutedProgress}
        label="swee-overall-response" strokeWidth={7}
        zeroLabelOpacity={zeroLabel}
        marker={markerPop > 0 ? {second: PEAK, value: PEAK_SCORE, pop: markerPop, label: `Peak ${mmss(PEAK)}`} : undefined}
        highlightSecond={frame >= PEAK_CLICK ? clamp(clipSecond, 0, TO) : undefined}
      />

      <div style={{
        position: 'absolute', right: 1920 - CHART.x - CHART.w, top: CHART.y - 44, display: 'flex', alignItems: 'center', gap: 30,
        fontFamily, fontSize: 28, fontWeight: 500, opacity: legendOpacity, pointerEvents: 'none', whiteSpace: 'nowrap',
      }}>
        <span style={{display: 'flex', alignItems: 'center', gap: 12, color: C.text}}>
          <svg width={50} height={8} aria-hidden>
            <line x1={2} y1={4} x2={48} y2={4} stroke={C.hot} strokeWidth={5} strokeLinecap="round" />
          </svg>
          with sound
        </span>
        <span style={{display: 'flex', alignItems: 'center', gap: 12, color: MUTED_INK}}>
          <svg width={50} height={8} aria-hidden>
            <line x1={2} y1={4} x2={48} y2={4} stroke={MUTED_INK} strokeWidth={4} strokeDasharray="9 7" strokeLinecap="round" />
          </svg>
          sound off
        </span>
      </div>

      <Ring x={peak.x} y={peak.y} p={clamp((frame - PEAK_CLICK) / 18)} />
      {cursorOpacity > 0.01 ? <Cursor x={cursor.x} y={cursor.y} opacity={cursorOpacity} pressed={pressed} /> : null}

      <div style={{position: 'absolute', inset: 0, background: '#000', opacity: 1 - easeOut(frame, 0, 12), pointerEvents: 'none', zIndex: 100}} />
    </Stage>
  );
};
