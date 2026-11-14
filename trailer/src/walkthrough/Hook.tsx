import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {AbsoluteFill, Easing, OffthreadVideo, interpolate, staticFile, useCurrentFrame} from 'remotion';
import bunny from '../bunny.json';
import {Graph} from '../edit/Graph';
import {C, fontFamily} from '../edit/ui';
import {Cortex} from '../scenes/brain/Cortex';
import {useBrainData} from './brainData';
import {FPS, HOOK, REPRISE, sec} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
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

const VIDEO_W = 1440;
const VIDEO_H = 810;
const SMALL = {x: 100, y: 150, w: 880, h: 495};
const BRAIN = {x: 1000, y: 90, w: 840, h: 610};
const GRAPH = {x: 100, y: 740, w: 1720, h: 230};
const RANGE: [number, number] = [-0.12, 0.24];
const ZERO_Y = 12 + ((RANGE[1] - 0) / (RANGE[1] - RANGE[0])) * (GRAPH.h - 24);

export const Hook: React.FC<{mode?: 'hook' | 'reprise'}> = ({mode = 'hook'}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const scene = mode === 'hook' ? HOOK : REPRISE;
  const length = scene.end - scene.start;
  const sourceStart = mode === 'hook' ? 12 : 44;
  // Reprise: the whole 55 s response, drawn up to the playing second (rising into its final peak).
  const axisFrom = mode === 'hook' ? sourceStart : 0;
  const axisTo = mode === 'hook' ? sourceStart + length : 54;
  const second = sourceStart + t;
  const data = useBrainData();

  const revealAt = mode === 'hook' ? HOOK.reveal - HOOK.start : 0;
  const glide = easeIO(t, revealAt, revealAt + 1.1);
  const push = mode === 'hook' ? 1 + 0.03 * easeIO(t, 0, Math.max(0.001, revealAt)) : 1;
  const shown = easeOut(t, revealAt, revealAt + 1.1);
  const brainIn = easeOut(t, revealAt, revealAt + 1.1);

  const vx = lerp((1920 - VIDEO_W) / 2, SMALL.x, glide);
  const vy = lerp((1080 - VIDEO_H) / 2, SMALL.y, glide);
  const vw = lerp(VIDEO_W, SMALL.w, glide);
  const vh = lerp(VIDEO_H, SMALL.h, glide);

  const progress = clamp(t / length);
  const draw = mode === 'hook' && t < revealAt ? 0 : clamp((second - axisFrom) / (axisTo - axisFrom));

  return <AbsoluteFill style={{background: '#000', fontFamily, color: C.text, overflow: 'hidden'}}>
    <div style={{position: 'absolute', left: GRAPH.x, top: GRAPH.y, width: GRAPH.w, height: GRAPH.h, opacity: shown}}>
      <div style={{
        position: 'absolute', left: 10, top: ZERO_Y, width: GRAPH.w - 20,
        borderTop: '1px dashed rgba(160,166,175,.55)',
      }} />
      <Graph
        values={bunny.signals.overall as number[]}
        from={axisFrom}
        to={axisTo}
        range={RANGE}
        width={GRAPH.w}
        height={GRAPH.h}
        progress={draw}
        playhead={second}
        color={C.hot}
        axes={false}
        label={`${mode}-response`}
      />
      <div style={{
        position: 'absolute', left: 10, top: ZERO_Y - 31, fontSize: 24,
        color: C.muted, letterSpacing: '-0.005em',
      }}>typical viewing</div>
    </div>

    {data && <div style={{
      position: 'absolute', left: BRAIN.x, top: BRAIN.y, width: BRAIN.w, height: BRAIN.h,
      opacity: brainIn, transform: `translateY(${(1 - brainIn) * 12}px)`, pointerEvents: 'none',
    }}>
      <ThreeCanvas
        width={BRAIN.w}
        height={BRAIN.h}
        camera={{fov: 28, near: .1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
        gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
        dpr={1}
      >
        <Cortex data={data} second={second} progress={progress} />
      </ThreeCanvas>
    </div>}

    <div style={{
      position: 'absolute', left: SMALL.x, top: SMALL.y + SMALL.h + 16, opacity: shown,
      fontSize: 22, color: C.muted, letterSpacing: '-0.005em',
    }}>Big Buck Bunny · predicted response</div>

    <div style={{
      position: 'absolute', left: vx, top: vy, width: vw, height: vh,
      borderRadius: 14, overflow: 'hidden', background: '#000',
      boxShadow: '0 40px 120px rgba(0,0,0,.85), 0 0 0 1px rgba(255,255,255,.06)',
    }}>
      <OffthreadVideo
        src={staticFile('bbb_720p_55s.mp4')}
        trimBefore={sec(sourceStart)}
        muted
        style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${push})`}}
      />
    </div>
  </AbsoluteFill>;
};
