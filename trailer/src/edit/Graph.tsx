import React, {useId} from 'react';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const at = (values: number[], time: number) => {
  const t = clamp(time, 0, values.length - 1);
  const i = Math.floor(t);
  const u = t - i;
  return values[i] + (values[Math.min(i + 1, values.length - 1)] - values[i]) * u;
};

const signed = (n: number, digits = 2) => `${n >= 0 ? '+' : '\u2212'}${Math.abs(n).toFixed(digits)}`;

export type GraphProps = {
  values: number[];
  comparison?: number[];
  valuesOffset?: number;
  comparisonOffset?: number;
  from: number;
  to: number;
  range: [number, number];
  width: number;
  height: number;
  progress?: number;
  comparisonProgress?: number;
  color?: string;
  comparisonColor?: string;
  comparisonDashed?: boolean;
  axes?: boolean;
  label: string;
  marker?: {second: number; label: string; size?: number; pop?: number};
  comparisonMarker?: {second: number; label: string};
  playhead?: number;
  zeroLabel?: string;
  strokeWidth?: number;
  comparisonStrokeWidth?: number;
  comparisonGlow?: number;
  background?: boolean;
};

export const Graph: React.FC<GraphProps> = ({
  values,
  comparison,
  valuesOffset,
  comparisonOffset,
  from,
  to,
  range,
  width,
  height,
  progress = 1,
  comparisonProgress = 1,
  color = '#FF8A2A',
  comparisonColor = '#6F7E99',
  comparisonDashed = false,
  axes = true,
  label,
  marker,
  comparisonMarker,
  playhead,
  zeroLabel,
  strokeWidth = 5,
  comparisonStrokeWidth = strokeWidth * .78,
  comparisonGlow = 0,
  background = false,
}) => {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const padL = axes ? 62 : 10;
  const padR = axes ? 30 : 10;
  const padT = axes ? 28 : 12;
  const padB = axes ? 43 : 12;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const x = (second: number) => padL + ((second - from) / (to - from)) * innerW;
  const y = (value: number) => padT + ((range[1] - value) / (range[1] - range[0])) * innerH;
  const zero = y(0);
  const mainOffset = valuesOffset ?? from;
  const compareOffset = comparisonOffset ?? from;

  const end = from + (to - from) * clamp(progress);
  const path = (data: number[], finish: number, offset: number) => {
    const value = (second: number) => at(data, offset + (second - from));
    const points: Array<[number, number]> = [[x(from), y(value(from))]];
    for (let second = Math.ceil(from); second <= Math.floor(finish); second++) {
      const i = offset + (second - from);
      if (i >= 0 && i < data.length) points.push([x(second), y(at(data, i))]);
    }
    points.push([x(finish), y(value(finish))]);
    return points.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(2)},${py.toFixed(2)}`).join(' ');
  };

  const main = path(values, end, mainOffset);
  const compare = comparison ? path(comparison, from + (to - from) * clamp(comparisonProgress), compareOffset) : '';
  const fill = `${main} L${x(end).toFixed(2)},${zero.toFixed(2)} L${x(from).toFixed(2)},${zero.toFixed(2)} Z`;
  const ticks = [range[0], range[0] / 2, 0, range[1] / 2, range[1]].filter((v, i, a) => a.indexOf(v) === i);
  const seconds = Array.from({length: Math.floor(to) - Math.ceil(from) + 1}, (_, i) => Math.ceil(from) + i)
    .filter((_, i) => i % Math.max(1, Math.round((to - from) / 6)) === 0);

  return <svg aria-label={label} width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{overflow: 'visible', display: 'block'}}>
    <defs>
      <linearGradient id={`fill-${uid}`} x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor={color} stopOpacity=".2" />
        <stop offset="1" stopColor={color} stopOpacity="0" />
      </linearGradient>
      <filter id={`glow-${uid}`} x="-20%" y="-50%" width="140%" height="200%">
        <feGaussianBlur stdDeviation="9" />
      </filter>
    </defs>
    {background && <rect x={0} y={0} width={width} height={height} fill="#0E0F11" opacity=".34" rx="4" />}
    {axes && ticks.map((tick) => <g key={tick}>
      <line x1={padL} x2={width - padR} y1={y(tick)} y2={y(tick)} stroke={tick === 0 ? '#555A63' : '#292B30'} strokeDasharray={tick === 0 ? '3 8' : undefined} strokeWidth={tick === 0 ? 1.6 : 1} />
      <text x={padL - 15} y={y(tick) + 6} fill="#A0A6AF" fontSize="17" textAnchor="end" fontVariant="tabular-nums">{signed(tick)}</text>
    </g>)}
    {axes && seconds.map((second) => <text key={second} x={x(second)} y={height - 10} fill="#A0A6AF" fontSize="17" textAnchor="middle" fontVariant="tabular-nums">{second}s</text>)}
    <path d={fill} fill={`url(#fill-${uid})`} opacity={.9} />
    <path d={main} fill="none" stroke={color} strokeWidth={strokeWidth * 4} strokeOpacity=".13" strokeLinejoin="round" strokeLinecap="round" filter={`url(#glow-${uid})`} />
    <path d={main} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
    {comparison && comparisonGlow > 0 && <path d={compare} fill="none" stroke={comparisonColor} strokeWidth={comparisonStrokeWidth * 4} strokeOpacity={comparisonGlow * .13} strokeLinejoin="round" strokeLinecap="round" filter={`url(#glow-${uid})`} />}
    {comparison && <path d={compare} fill="none" stroke={comparisonColor} strokeWidth={comparisonStrokeWidth} strokeDasharray={comparisonDashed ? '12 11' : undefined} strokeLinejoin="round" strokeLinecap="round" />}
    {axes && zeroLabel && <text x={padL + 12} y={zero - 9} fill="#A0A6AF" fontSize="20" textAnchor="start" letterSpacing=".02em">{zeroLabel}</text>}
    {playhead !== undefined && playhead >= from && playhead <= to && (() => {
      const px = x(playhead);
      const py = y(at(values, mainOffset + (playhead - from)));
      return <g>
        <line x1={px} x2={px} y1={padT} y2={height - padB} stroke="#ECEDEF" strokeOpacity=".28" strokeWidth="1.5" />
        <circle cx={px} cy={py} r="6" fill={color} stroke="#0E0F11" strokeWidth="2.5" />
      </g>;
    })()}
    {marker && marker.second <= end + .001 && (() => {
      const px = x(marker.second);
      const py = y(at(values, mainOffset + (marker.second - from)));
      const pop = Math.max(0, marker.pop ?? 1);
      const size = marker.size ?? 24;
      const dx = px < width * .66 ? px + size * .8 : px - size * .8;
      const anchor = px < width * .66 ? 'start' : 'end';
      return <g>
        <line x1={px} x2={px} y1={padT} y2={height - padB} stroke="#ECEDEF" strokeOpacity=".17" />
        <g transform={`translate(${px} ${py}) scale(${pop}) translate(${-px} ${-py})`}>
          <circle cx={px} cy={py} r="15" fill={color} opacity=".16" />
          <circle cx={px} cy={py} r="6" fill={color} stroke="#0E0F11" strokeWidth="2.5" />
          <text x={dx} y={py - size * .8} fill="#ECEDEF" fontSize={size} fontWeight="500" textAnchor={anchor} fontVariant="tabular-nums">{marker.label}</text>
        </g>
      </g>;
    })()}
    {comparisonMarker && comparison && comparisonMarker.second <= from + (to - from) * clamp(comparisonProgress) + .001 && (() => {
      const px = x(comparisonMarker.second);
      const py = y(at(comparison, compareOffset + (comparisonMarker.second - from)));
      return <g>
        <circle cx={px} cy={py} r="6" fill={comparisonColor} stroke="#0E0F11" strokeWidth="2.5" />
        <text x={px < width * .66 ? px + 19 : px - 19} y={py + 34} fill={comparisonColor} fontSize="22" fontWeight="500" textAnchor={px < width * .66 ? 'start' : 'end'} fontVariant="tabular-nums">{comparisonMarker.label}</text>
      </g>;
    })()}
  </svg>;
};

export {signed, at};
