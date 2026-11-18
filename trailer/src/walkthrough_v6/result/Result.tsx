import React from 'react';
import {Sequence, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {Graph} from '../../edit/Graph';
import {BrainView, Cursor, PhoneClip, Ring, Stage, clamp, easeBack, easeInOut, easeOut, lerp} from '../common';
import {CURSOR_IN, DURATION, FPS, OVERALL, PEAK, PEAK_CLICK, PEAK_MARK, PEAK_SCORE, RANGE, SHOT, TO} from './timeline';

// Fixed, centred layout: clip and brain in a band on top, the chart under them. Between
// shots only brightness changes.
const CLIP = {x: 560, y: 36, w: 246};
const BRAIN = {x: 900, y: 20, size: 470};
const CHART = {x: 150, y: 545, w: 1620, h: 320};
const PAD = {l: 10, r: 10, t: 14, b: 14};

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
  const zeroY = yAt(0);

  const zeroLabel = easeOut(frame, SHOT.b.start + 60, SHOT.b.start + 84);
  const lineProgress = easeInOut(frame, SHOT.b.start + 4, SHOT.b.start + 80);

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

      <div style={{position: 'absolute', left: CHART.x, top: CHART.y, width: CHART.w, height: CHART.h, zIndex: 0}}>
        {lineProgress > 0.004 ? <Graph
          values={OVERALL} from={0} to={TO} range={RANGE} width={CHART.w} height={CHART.h}
          progress={lineProgress} color={C.hot} axes={false} label="swee-overall-response" strokeWidth={7}
        /> : null}
        <svg width={CHART.w} height={CHART.h + 80} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
          <line x1={PAD.l} x2={CHART.w - PAD.r} y1={zeroY} y2={zeroY} stroke={C.text} strokeWidth={2.5}
            strokeDasharray="16 14" opacity={0.5} />
          <text x={xAt(41)} y={zeroY - 18} fill={C.text} fontFamily={fontFamily} fontSize={44} fontWeight={500}
            textAnchor="end" opacity={0.6 * zeroLabel}>typical viewing</text>
          {[0, 10, 20, 30, 40].map((second) => (
            <text key={second} x={xAt(second)} y={CHART.h + 54} fill={C.muted} fontFamily={fontFamily} fontSize={44}
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
            return <line x1={hx} x2={hx} y1={PAD.t} y2={CHART.h - PAD.b} stroke={C.text} strokeOpacity={0.4} strokeWidth={3} />;
          })() : null}
        </svg>
      </div>

      <Ring x={peak.x} y={peak.y} p={clamp((frame - PEAK_CLICK) / 18)} />
      {cursorOpacity > 0.01 ? <Cursor x={cursor.x} y={cursor.y} opacity={cursorOpacity} pressed={pressed} /> : null}

      <div style={{position: 'absolute', inset: 0, background: '#000', opacity: 1 - easeOut(frame, 0, 12), pointerEvents: 'none', zIndex: 100}} />
    </Stage>
  );
};
