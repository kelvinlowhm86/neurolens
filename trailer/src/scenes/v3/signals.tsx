import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../../edit/ui';

export const RING: ReadonlyArray<readonly [number, number]> = [
  [350, 222], [852, 158], [1470, 246], [1490, 702], [966, 872], [372, 848], [178, 512],
];

export const GROUPS = [
  {title: 'See', series: 'see', keys: ['faces', 'bodies', 'scenes', 'visual'], x: 480, color: C.hot},
  {title: 'Hear', series: 'hear', keys: ['sound'], x: 960, color: C.cool},
  {title: 'Understand', series: 'understand', keys: ['speech', 'social'], x: 1440, color: C.text},
] as const;

export const COLUMN_POS: ReadonlyArray<readonly [number, number]> = [
  [480, 280], [480, 376], [480, 472], [480, 568],
  [960, 280], [1440, 280], [1440, 376],
];

export const GROUP_TITLE_Y = 166;

export const SignalField: React.FC = () =>
  <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 50%, rgba(255,138,42,.04), transparent 54%), radial-gradient(ellipse at 18% 16%, rgba(111,126,153,.15), transparent 40%), radial-gradient(ellipse at 84% 82%, rgba(111,126,153,.12), transparent 42%)'}} />
  </AbsoluteFill>;

export const GroupTitle: React.FC<{title: string; x: number; color: string; opacity?: number}> = ({title, x, color, opacity = 1}) =>
  <div style={{position: 'absolute', left: x, top: GROUP_TITLE_Y, width: 480, transform: 'translate(-50%,-50%)', textAlign: 'center', opacity, pointerEvents: 'none'}}>
    <div style={{fontSize: 72, lineHeight: 1, fontWeight: 600, letterSpacing: '-.055em', textShadow: '0 8px 38px #000'}}>{title}</div>
    <div style={{margin: '14px auto 0', width: 84, height: 3, background: color, borderRadius: 2, opacity: .8}} />
  </div>;

export const SignalLabel: React.FC<{
  name: string; x: number; y: number;
  nameSize: number; opacity?: number; scale?: number;
}> = ({name, x, y, nameSize, opacity = 1, scale = 1}) =>
  <div style={{position: 'absolute', left: x, top: y, width: 620, transform: `translate(-50%,-50%) scale(${scale})`, textAlign: 'center', opacity, pointerEvents: 'none'}}>
    <div style={{fontSize: nameSize, lineHeight: 1.02, fontWeight: 600, letterSpacing: '-.055em', textShadow: '0 8px 38px #000'}}>{name}</div>
  </div>;
