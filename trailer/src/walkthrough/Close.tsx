import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {Cortex} from '../scenes/brain/Cortex';
import {useBrainData} from './brainData';
import {CLOSE, FPS} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const easeIO = (t: number, a: number, b: number) =>
  interpolate(t, [a, b], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
const easeOut = (t: number, a: number, b: number) =>
  interpolate(t, [a, b], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

export const Close: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const length = CLOSE.end - CLOSE.start;
  const data = useBrainData();

  const textIn = easeOut(t, 0, .8);
  const fadeOut = easeIO(t, length - .8, length);

  return <AbsoluteFill style={{background: '#000', fontFamily, color: C.text, overflow: 'hidden'}}>
    {data && <ThreeCanvas
      width={1400}
      height={1080}
      style={{position: 'absolute', left: 260, top: 0, opacity: .4, pointerEvents: 'none'}}
      camera={{fov: 28, near: .1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
      gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
      dpr={1}
    >
      <Cortex data={data} second={12} progress={clamp(t / length)} />
    </ThreeCanvas>}

    <AbsoluteFill style={{
      alignItems: 'center', justifyContent: 'center', flexDirection: 'column',
      opacity: textIn, transform: `translateY(${(1 - textIn) * 10}px)`,
    }}>
      <div style={{fontSize: 176, lineHeight: 1, fontWeight: 600, letterSpacing: '-0.055em'}}>NeuroLens</div>
      <div style={{marginTop: 30, fontSize: 28, color: C.muted, letterSpacing: '-0.01em'}}>
        Explore the samples at the link below
      </div>
    </AbsoluteFill>

    <AbsoluteFill style={{background: '#000', opacity: fadeOut, pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
