import React from 'react';
import {Easing, useCurrentFrame} from 'remotion';
import {cropAt, project} from './camera';
import {BAR, CAMS, FPS, STAGE} from './timing';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const lerp = (a: number, b: number, p: number) => a + (b - a) * p;

const BoxScrim: React.FC<{
  left: number; top: number; width: number; height: number; opacity: number;
}> = ({left, top, width, height, opacity}) => {
  if (width <= 0 || height <= 0 || opacity <= 0) return null;
  return <div style={{
    position: 'absolute',
    left,
    top,
    width,
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
    <BoxScrim left={STAGE.x} top={contentTop} width={STAGE.w}
      height={holeTop - contentTop} opacity={opacity} />
    <BoxScrim left={STAGE.x} top={holeBottom} width={STAGE.w}
      height={contentBottom - holeBottom} opacity={opacity} />
  </>;
};

export type Rect = {x: number; y: number; w: number; h: number};

// Dim everything outside one source-space rectangle. Used when the named thing
// is smaller than a full-width band (a tooltip-free peak, a legend label, a
// single advice sentence, a stretch of a chart).
export const FocusSpotlight: React.FC<{
  from: number;
  to: number;
  strength?: number;
  fade?: number;
  rect?: Rect;
  rectAt?: (t: number) => Rect;
}> = ({from, to, strength = 0.6, fade = 0.45, rect, rectAt}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const opacity = Math.min(clamp((t - from) / fade), clamp((to - t) / fade)) * strength;
  if (opacity <= 0) return null;

  const hole = rectAt ? rectAt(t) : rect;
  if (!hole) return null;
  const crop = cropAt(CAMS, t);
  const topLeft = project({x: hole.x, y: hole.y}, crop, STAGE);
  const bottomRight = project({x: hole.x + hole.w, y: hole.y + hole.h}, crop, STAGE);
  const contentTop = STAGE.y + BAR;
  const contentBottom = STAGE.y + STAGE.h;
  const contentLeft = STAGE.x;
  const contentRight = STAGE.x + STAGE.w;
  const top = clamp(topLeft.y, contentTop, contentBottom);
  const bottom = clamp(bottomRight.y, contentTop, contentBottom);
  const left = clamp(topLeft.x, contentLeft, contentRight);
  const right = clamp(bottomRight.x, contentLeft, contentRight);

  return <>
    <BoxScrim left={contentLeft} top={contentTop} width={STAGE.w}
      height={top - contentTop} opacity={opacity} />
    <BoxScrim left={contentLeft} top={bottom} width={STAGE.w}
      height={contentBottom - bottom} opacity={opacity} />
    <BoxScrim left={contentLeft} top={top} width={left - contentLeft}
      height={bottom - top} opacity={opacity} />
    <BoxScrim left={right} top={top} width={contentRight - right}
      height={bottom - top} opacity={opacity} />
  </>;
};

// Smooth glide between two holes of the same height, for the compare chart.
export const glideRect = (t: number, from: number, to: number, opening: Rect, middle: Rect): Rect => {
  const p = Easing.inOut(Easing.cubic)(clamp((t - from) / (to - from)));
  return {
    x: lerp(opening.x, middle.x, p),
    y: lerp(opening.y, middle.y, p),
    w: lerp(opening.w, middle.w, p),
    h: lerp(opening.h, middle.h, p),
  };
};
