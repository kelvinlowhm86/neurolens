import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {FADE} from './timeline';

export const Fade: React.FC<{total: number; background?: string; children: React.ReactNode}> = ({total, background = 'transparent', children}) => {
  const f = useCurrentFrame();
  const opacity = interpolate(f, [0, FADE, total, total + FADE], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  return <AbsoluteFill style={{opacity, background}}>{children}</AbsoluteFill>;
};
