import React from 'react';
import {C, fontFamily} from '../../edit/ui';
import {Graph, at} from '../../edit/Graph';

// The response chart used by the result and the honest-limit section. Same line, baseline,
// ticks and peak marker in both places; the outro adds the muted comparison and a scrubbing cursor.

const PAD = {l: 10, r: 10, t: 14, b: 14};
const BG = '#0B0C0F';
const MUTED_INK = '#6F7E99';
const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

export type ChartMarker = {second: number; value: number; pop: number; label: string};

export type ChartProps = {
  x: number;
  y: number;
  w: number;
  h: number;
  values: number[];
  to: number;
  range: [number, number];
  progress?: number;
  colour?: string;
  comparison?: number[];
  comparisonProgress?: number;
  comparisonDashed?: boolean;
  comparisonColour?: string;
  comparisonGlow?: number;
  strokeWidth?: number;
  label: string;
  zeroLabelOpacity?: number;
  marker?: ChartMarker;
  highlightSecond?: number;
  cursor?: {second: number; comparison?: boolean};
  band?: {from: number; to: number; opacity: number};
};

export const Chart: React.FC<ChartProps> = ({
  x,
  y,
  w,
  h,
  values,
  to,
  range,
  progress = 1,
  colour = C.hot,
  comparison,
  comparisonProgress,
  comparisonDashed,
  comparisonColour = MUTED_INK,
  comparisonGlow,
  strokeWidth = 7,
  label,
  zeroLabelOpacity = 1,
  marker,
  highlightSecond,
  cursor,
  band,
}) => {
  const innerW = w - PAD.l - PAD.r;
  const innerH = h - PAD.t - PAD.b;
  const xAt = (second: number) => PAD.l + (second / to) * innerW;
  const yAt = (value: number) => PAD.t + ((range[1] - value) / (range[1] - range[0])) * innerH;
  const zeroY = yAt(0);
  const seconds = [0, 10, 20, 30, 40];

  return (
    <div style={{position: 'absolute', left: x, top: y, width: w, height: h, zIndex: 0}}>
      {band && band.opacity > 0.001 ? (
        <svg width={w} height={h} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', zIndex: 0}}>
          <rect
            x={xAt(band.from)}
            y={PAD.t}
            width={xAt(band.to) - xAt(band.from)}
            height={h - PAD.t - PAD.b}
            rx={12}
            fill={C.hot}
            opacity={band.opacity}
          />
        </svg>
      ) : null}
      {progress > 0.004 ? (
        <div style={{position: 'relative', zIndex: 1}}>
          <Graph
            values={values}
            comparison={comparison}
            from={0}
            to={to}
            range={range}
            width={w}
            height={h}
            progress={progress}
            comparisonProgress={comparisonProgress}
            comparisonDashed={comparisonDashed}
            comparisonColor={comparisonColour}
            comparisonGlow={comparisonGlow}
            color={colour}
            axes={false}
            label={label}
            strokeWidth={strokeWidth}
          />
        </div>
      ) : null}
      <svg width={w} height={h + 80} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', zIndex: 2}}>
        <line x1={PAD.l} x2={w - PAD.r} y1={zeroY} y2={zeroY} stroke={C.text} strokeWidth={2.5}
          strokeDasharray="16 14" opacity={0.5} />
        <text x={xAt(to - 2)} y={zeroY - 18} fill={C.text} fontFamily={fontFamily} fontSize={44} fontWeight={500}
          textAnchor="end" opacity={0.6 * zeroLabelOpacity}>typical viewing</text>
        {seconds.map((second) => (
          <text key={second} x={xAt(second)} y={h + 54} fill={C.muted} fontFamily={fontFamily} fontSize={44}
            textAnchor={second === 0 ? 'start' : 'middle'} style={{fontVariantNumeric: 'tabular-nums'}}>{second}s</text>
        ))}
        {highlightSecond !== undefined ? (() => {
          const hx = xAt(clamp(highlightSecond, 0, to));
          return <line x1={hx} x2={hx} y1={PAD.t} y2={h - PAD.b} stroke={C.text} strokeOpacity={0.4} strokeWidth={3} />;
        })() : null}
        {cursor ? (() => {
          const cSecond = clamp(cursor.second, 0, to);
          const px = xAt(cSecond);
          const mainY = yAt(at(values, cSecond));
          const mutedY = cursor.comparison && comparison ? yAt(at(comparison, cSecond)) : null;
          return (
            <g>
              <line x1={px} x2={px} y1={PAD.t} y2={h - PAD.b} stroke={C.text} strokeOpacity={0.5} strokeWidth={3} />
              {mutedY !== null ? <circle cx={px} cy={mutedY} r={8} fill={comparisonColour} stroke={BG} strokeWidth={3} /> : null}
              <circle cx={px} cy={mainY} r={10} fill={colour} stroke={BG} strokeWidth={3} />
            </g>
          );
        })() : null}
        {marker && marker.pop > 0 ? (
          <g transform={`translate(${xAt(marker.second)} ${yAt(marker.value)}) scale(${marker.pop})`}>
            <circle r={22} fill={colour} opacity={0.2} />
            <circle r={10} fill={colour} stroke={C.text} strokeWidth={3} />
            <text x={0} y={-34} fill={C.text} fontFamily={fontFamily} fontSize={44} fontWeight={600} textAnchor="start">
              {marker.label}
            </text>
          </g>
        ) : null}
      </svg>
    </div>
  );
};
