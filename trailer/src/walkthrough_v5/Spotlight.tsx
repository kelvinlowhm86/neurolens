import React from 'react';
import {useCurrentFrame} from 'remotion';
import {cropAt, project} from './camera';
import {BAR, CAMS, FPS, STAGE} from './timing';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const Scrim: React.FC<{top: number; height: number; opacity: number}> = ({top, height, opacity}) => {
  if (height <= 0 || opacity <= 0) return null;
  return <div style={{
    position: 'absolute',
    left: STAGE.x,
    top,
    width: STAGE.w,
    height,
    background: '#000',
    opacity,
  }} />;
};

export const Spotlight: React.FC<{
  from: number;
  to: number;
  sourceTop: number;
  sourceBottom: number;
  strength?: number;
}> = ({from, to, sourceTop, sourceBottom, strength = 0.55}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const opacity = Math.min(clamp((t - from) / 0.45), clamp((to - t) / 0.45)) * strength;
  if (opacity <= 0) return null;

  const crop = cropAt(CAMS, t);
  const topLeft = project({x: 0, y: sourceTop}, crop, STAGE);
  const bottomRight = project({x: 0, y: sourceBottom}, crop, STAGE);
  const contentTop = STAGE.y + BAR;
  const contentBottom = STAGE.y + STAGE.h;
  const holeTop = clamp(topLeft.y, contentTop, contentBottom);
  const holeBottom = clamp(bottomRight.y, contentTop, contentBottom);

  return <>
    <Scrim top={contentTop} height={holeTop - contentTop} opacity={opacity} />
    <Scrim top={holeBottom} height={contentBottom - holeBottom} opacity={opacity} />
  </>;
};
