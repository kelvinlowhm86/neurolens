import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {ThreeCanvas} from '@remotion/three';
import {ScannerWorld} from './scanner/ScannerWorld';

/** A completely procedural, frame-addressable opening shot. No external assets. */
export const Scanner: React.FC = () => {
  const frame = useCurrentFrame();
  const {width, height, durationInFrames} = useVideoConfig();
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, durationInFrames - 1)));
  return <AbsoluteFill style={{background: '#0E0F11'}}>
    <ThreeCanvas width={width} height={height}
      camera={{position: [8.6, 5.3, 12.6], fov: 34, near: 0.1, far: 65}}
      gl={{antialias: true, alpha: false, powerPreference: 'high-performance'}}
      shadows>
      <ScannerWorld progress={progress}/>
    </ThreeCanvas>
    <AbsoluteFill style={{pointerEvents: 'none', background:
      'radial-gradient(ellipse at 52% 41%, transparent 30%, rgba(7,9,13,0.14) 64%, rgba(7,9,13,0.7) 100%)'}}/>
    <AbsoluteFill style={{pointerEvents: 'none', background:
      'linear-gradient(0deg, rgba(14,15,17,0.94) 0%, rgba(14,15,17,0.42) 15%, transparent 37%)'}}/>
  </AbsoluteFill>;
};
