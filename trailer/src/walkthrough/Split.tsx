import React from 'react';
import {AbsoluteFill, Easing, interpolate, interpolateColors, useCurrentFrame} from 'remotion';
import bunny from '../bunny.json';
import {Graph} from '../edit/Graph';
import {C, fontFamily} from '../edit/ui';
import {FPS, MUTE_DROP, SPLIT} from './timeline';

const easeIO = (t: number, a: number, b: number) =>
  interpolate(t, [a, b], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

const overall = bunny.signals.overall as number[];
const muted = bunny.muted.overall as number[];
const shared = Math.min(overall.length, muted.length);
const withSound = overall.slice(0, shared);
const withoutSound = muted.slice(0, shared);
const all = [...withSound, ...withoutSound];
const low = Math.min(...all);
const high = Math.max(...all);
const pad = (high - low) * .10 || .04;
const RANGE: [number, number] = [low - pad, high + pad];
const GRAPH = {x: 110, y: 210, w: 1700, h: 560};
const ZERO_Y = 12 + ((RANGE[1] - 0) / (RANGE[1] - RANGE[0])) * (GRAPH.h - 24);

export const Split: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const muteAt = MUTE_DROP - SPLIT.start;
  const tick = easeIO(t, muteAt, muteAt + .25);
  const morph = easeIO(t, muteAt, muteAt + 1.2);
  const comparison = withSound.map((v, i) => v + (withoutSound[i] - v) * morph);

  return <AbsoluteFill style={{background: '#000', fontFamily, color: C.text}}>
    <div style={{
      position: 'absolute', left: GRAPH.x, top: 100, width: GRAPH.w,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    }}>
      <div style={{fontSize: 44, fontWeight: 600, letterSpacing: '-0.03em'}}>Brain response over time</div>
      <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
        <div style={{
          width: 32, height: 32, boxSizing: 'border-box', borderRadius: 8,
          border: `2px solid ${interpolateColors(tick, [0, 1], ['#6B7079', C.hot])}`,
          background: `rgba(255,138,42,${tick})`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <svg width={20} height={20} viewBox="0 0 20 20" style={{opacity: tick, transform: `scale(${.7 + .3 * tick})`}}>
            <path d="M4 10.5 L8.5 15 L16 5.5" fill="none" stroke="#000"
              strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <span style={{fontSize: 30, letterSpacing: '-0.02em'}}>Without sound</span>
      </div>
    </div>

    <div style={{position: 'absolute', left: GRAPH.x, top: GRAPH.y, width: GRAPH.w, height: GRAPH.h}}>
      <div style={{
        position: 'absolute', left: 10, top: ZERO_Y, width: GRAPH.w - 20,
        borderTop: '1px dashed rgba(160,166,175,.45)',
      }} />
      <Graph
        values={withSound}
        comparison={comparison}
        from={0}
        to={shared - 1}
        range={RANGE}
        width={GRAPH.w}
        height={GRAPH.h}
        progress={1}
        comparisonProgress={1}
        color={C.hot}
        comparisonColor="rgba(236,237,239,.7)"
        comparisonDashed
        axes={false}
        label="split-response"
      />
      <div style={{
        position: 'absolute', left: 10, top: ZERO_Y - 31, fontSize: 24,
        color: C.muted, letterSpacing: '-0.005em',
      }}>typical viewing</div>
    </div>

    <div style={{
      position: 'absolute', left: GRAPH.x, top: 806, display: 'flex', gap: 52,
      alignItems: 'center', fontSize: 24, letterSpacing: '-0.005em',
    }}>
      <div style={{display: 'flex', alignItems: 'center', gap: 14}}>
        <span style={{width: 44, height: 3, borderRadius: 2, background: C.hot, display: 'inline-block'}} />
        <span style={{color: C.text}}>with sound</span>
      </div>
      <div style={{display: 'flex', alignItems: 'center', gap: 14}}>
        <span style={{width: 44, borderTop: '3px dashed rgba(236,237,239,.7)', display: 'inline-block'}} />
        <span style={{color: C.text}}>without sound</span>
      </div>
    </div>
  </AbsoluteFill>;
};
