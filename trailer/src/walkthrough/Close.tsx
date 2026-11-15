import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {Cortex} from '../scenes/brain/Cortex';
import {useSweeBrain} from './brainData';
import {CLOSE, FPS} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const easeOut = (t: number, a: number, b: number) => interpolate(t, [a, b], [0, 1], {
  extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  easing: Easing.out(Easing.cubic),
});
const easeIO = (t: number, a: number, b: number) => interpolate(t, [a, b], [0, 1], {
  extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  easing: Easing.inOut(Easing.cubic),
});

export const Close: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const length = CLOSE.end - CLOSE.start;
  const data = useSweeBrain();

  const inFade = easeOut(t, 0, 0.7);
  const outFade = 1 - easeIO(t, length - 0.8, length);
  const word = easeOut(t, 1.4, 2.2);
  const sub = easeOut(t, 2.0, 2.8);
  const turn = clamp(t / length);

  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 46%, #101114 0%, #000 68%)', fontFamily, color: C.text, overflow: 'hidden'}}>
    {data && <ThreeCanvas
      width={1500} height={1080}
      style={{position: 'absolute', left: 210, top: -120, opacity: 0.25 * inFade, pointerEvents: 'none'}}
      camera={{fov: 28, near: 0.1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
      gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
      dpr={1}
    >
      <Cortex data={data} second={12} progress={0.12 + turn * 0.58} />
    </ThreeCanvas>}

    <AbsoluteFill style={{
      alignItems: 'center', justifyContent: 'center', flexDirection: 'column',
      opacity: inFade * outFade,
    }}>
      <div style={{
        fontSize: 176, lineHeight: 1, fontWeight: 600, letterSpacing: '-0.055em',
        opacity: word, transform: `translateY(${(1 - word) * 14}px)`,
        textShadow: '0 12px 64px rgba(0,0,0,.85)',
      }}>NeuroLens</div>
      <div style={{
        marginTop: 260, fontSize: 32, fontWeight: 500, color: C.text, letterSpacing: '-0.01em',
        padding: '12px 30px', borderRadius: 999,
        background: 'rgba(13,14,17,.88)', border: `1px solid ${C.rule}`,
        boxShadow: '0 14px 40px rgba(0,0,0,.75)',
        opacity: sub, transform: `translateY(${(1 - sub) * 8}px)`,
      }}>
        Try the samples at the link below
      </div>
    </AbsoluteFill>

    <AbsoluteFill style={{background: '#000', opacity: 1 - inFade, pointerEvents: 'none'}} />
    <AbsoluteFill style={{background: '#000', opacity: 1 - outFade, pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
