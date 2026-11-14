import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {
  AbsoluteFill, Easing, OffthreadVideo, interpolate, interpolateColors, staticFile, useCurrentFrame,
} from 'remotion';
import bunny from '../bunny.json';
import {Graph} from '../edit/Graph';
import {C, fontFamily} from '../edit/ui';
import {Cortex} from '../scenes/brain/Cortex';
import {useBrainData} from './brainData';
import {FPS, FLOW, sec} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const easeOut = (t: number, a: number, b: number) =>
  interpolate(t, [a, b], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

type SignalKey = 'faces' | 'bodies' | 'scenes' | 'visual' | 'sound' | 'speech' | 'social';
const GROUPS: {title: string; start: number; rows: {name: string; series: SignalKey; at: number}[]}[] = [
  {title: 'Seeing', start: 2.2, rows: [
    {name: 'Faces', series: 'faces', at: 2.2},
    {name: 'Bodies', series: 'bodies', at: 2.32},
    {name: 'Scenes', series: 'scenes', at: 2.44},
    {name: 'Visual', series: 'visual', at: 2.56},
  ]},
  {title: 'Hearing', start: 2.68, rows: [
    {name: 'Sound', series: 'sound', at: 2.68},
  ]},
  {title: 'Understanding', start: 2.8, rows: [
    {name: 'Speech', series: 'speech', at: 2.8},
    {name: 'Social', series: 'social', at: 2.92},
  ]},
];

const seriesRange = (values: number[]): [number, number] => {
  const low = Math.min(...values);
  const high = Math.max(...values);
  return high > low ? [low, high] : [low - 0.01, high + 0.01];
};

const Arrow: React.FC<{p: number}> = ({p}) => {
  const head = clamp((p - .8) / .2);
  return <svg width={90} height={20} viewBox="0 0 90 20" style={{overflow: 'visible', flex: '0 0 90px', margin: '0 10px'}}>
    <line x1={0} y1={10} x2={74} y2={10} stroke={C.muted} strokeWidth={2}
      strokeDasharray={74} strokeDashoffset={74 * (1 - p)} opacity={p} />
    <path d="M 66 4 L 74 10 L 66 16" fill="none" stroke={C.muted} strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" opacity={head} />
  </svg>;
};

export const Flow: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const length = FLOW.end - FLOW.start;
  const data = useBrainData();

  const brainIn = easeOut(t, 1.0, 1.5);
  const arrowA = easeOut(t, 0.5, 1.0);
  const arrowB = easeOut(t, 1.7, 2.2);
  const pulse = t >= 2.8 && t <= 3.4 ? Math.sin(((t - 2.8) / 0.6) * Math.PI) : 0;

  return <AbsoluteFill style={{background: '#000', fontFamily, color: C.text, overflow: 'hidden'}}>
    <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
      <div style={{width: 520, display: 'flex', flexDirection: 'column', alignItems: 'center'}}>
        <div style={{
          width: 520, height: 293, borderRadius: 14, overflow: 'hidden', background: '#000',
          boxShadow: '0 24px 70px rgba(0,0,0,.75), 0 0 0 1px rgba(255,255,255,.06)',
        }}>
          <OffthreadVideo
            src={staticFile('bbb_720p_55s.mp4')}
            trimBefore={sec(12)}
            muted
            style={{width: '100%', height: '100%', objectFit: 'cover'}}
          />
        </div>
        <div style={{marginTop: 20, fontSize: 36, fontWeight: 600, letterSpacing: '-0.02em'}}>Your video</div>
      </div>

      <Arrow p={arrowA} />

      <div style={{
        width: 520, display: 'flex', flexDirection: 'column', alignItems: 'center',
        opacity: brainIn, transform: `translateY(${(1 - brainIn) * 12}px)`,
      }}>
        <div style={{width: 520, height: 480}}>
          {data && <ThreeCanvas
            width={520}
            height={480}
            camera={{fov: 28, near: .1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
            gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
            dpr={1}
          >
            <Cortex data={data} second={12 + t} progress={clamp(t / length)} />
          </ThreeCanvas>}
        </div>
        <div style={{
          marginTop: 0, fontSize: 36, fontWeight: 600, letterSpacing: '-0.02em',
          color: interpolateColors(pulse, [0, 1], [C.text, C.hot]),
        }}>TRIBE v2 · Meta</div>
      </div>

      <Arrow p={arrowB} />

      <div style={{width: 500, marginLeft: 20, display: 'flex', flexDirection: 'column'}}>
        {GROUPS.map((group) => {
          const titleP = easeOut(t, group.start, group.start + 0.4);
          return <div key={group.title} style={{marginBottom: 18}}>
            <div style={{
              fontSize: 36, fontWeight: 600, letterSpacing: '-0.02em', marginBottom: 6,
              opacity: titleP, transform: `translateY(${(1 - titleP) * 10}px)`,
            }}>{group.title}</div>
            {group.rows.map((row) => {
              const p = easeOut(t, row.at, row.at + 0.4);
              const values = bunny.signals[row.series] as number[];
              return <div key={row.name} style={{
                height: 50, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                opacity: p, transform: `translateY(${(1 - p) * 10}px)`,
              }}>
                <span style={{fontSize: 30, color: C.muted, letterSpacing: '-0.01em'}}>{row.name}</span>
                <div style={{width: 210, height: 44, overflow: 'hidden'}}>
                  <Graph
                    values={values}
                    from={0}
                    to={values.length - 1}
                    range={seriesRange(values)}
                    width={210}
                    height={44}
                    axes={false}
                    strokeWidth={2.5}
                    color={C.hot}
                    label={`${row.name}-spark`}
                  />
                </div>
              </div>;
            })}
          </div>;
        })}
      </div>
    </div>
  </AbsoluteFill>;
};
