import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {ease} from '../edit/anim';
import {
  Box, BoxState, Caption, Dot, Gate, Link, Pt, left, right,
} from './Diagram';

export const DURATION = 1050;

const easeInOut = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

const P = {
  browser: {x: 120, y: 580},
  website: {x: 470, y: 580},
  queue: {x: 820, y: 580},
  check: {x: 1160, y: 580},
  gpu: {x: 1500, y: 580},
  storage: {x: 820, y: 300},
};

const PATHS = {
  browserToWebsite: {from: right(P.browser), to: left(P.website)},
  browserToStorage: {from: right(P.browser), to: left(P.storage), bend: -110},
  websiteToQueue: {from: right(P.website), to: left(P.queue)},
  queueToCheck: {from: right(P.queue), to: left(P.check)},
  checkToGpu: {from: right(P.check), to: left(P.gpu)},
};

type Key = {at: number; state: BoxState};

const atState = (f: number, keys: Key[]) => {
  let index = 0;
  for (let i = 0; i < keys.length; i += 1) if (keys[i].at <= f) index = i;
  return {
    state: keys[index].state,
    at: keys[index].at,
    prev: index > 0 ? keys[index - 1].state : undefined,
  };
};

const WEBSITE: Key[] = [
  {at: 14, state: 'lit'},
  {at: 140, state: 'idle'},
  {at: 290, state: 'lit'},
  {at: 395, state: 'dim'},
];
const BROWSER: Key[] = [{at: 92, state: 'idle'}];
const STORAGE: Key[] = [{at: 106, state: 'idle'}, {at: 118, state: 'lit'}, {at: 245, state: 'idle'}];
const QUEUE: Key[] = [{at: 280, state: 'idle'}, {at: 292, state: 'lit'}];
const CHECK: Key[] = [{at: 646, state: 'idle'}, {at: 658, state: 'lit'}];
const GPU: Key[] = [{at: 690, state: 'idle'}];

const GATE_AT: Pt = {
  x: PATHS.checkToGpu.from.x + (PATHS.checkToGpu.to.x - PATHS.checkToGpu.from.x) * 0.72,
  y: PATHS.checkToGpu.from.y,
};

const Readout: React.FC<{frame: number}> = ({frame}) => {
  const length = ease(frame, 792, 804);
  const price = ease(frame, 900, 912);
  if (length <= 0) return null;
  return <div style={{
    position: 'absolute',
    left: 1065,
    top: 760,
    display: 'flex',
    alignItems: 'baseline',
    gap: 34,
    padding: '18px 30px 20px',
    borderRadius: 22,
    background: 'rgba(13,14,17,.92)',
    border: `1.5px solid ${C.rule}`,
    fontFamily,
    fontSize: 36,
    fontVariantNumeric: 'tabular-nums',
    whiteSpace: 'nowrap',
  }}>
    <span style={{opacity: length, color: C.text}}>length 0:44 <span style={{color: C.hot}}>✓</span></span>
    <span style={{opacity: price, color: C.text}}>price <span style={{color: C.hot}}>✓</span></span>
  </div>;
};

const JobCard: React.FC<{frame: number}> = ({frame}) => {
  const opacity = Math.min(ease(frame, 296, 302), 1 - ease(frame, 326, 338));
  if (opacity <= 0) return null;
  const y = interpolate(easeInOut(frame, 300, 328), [0, 1], [330, 455]);
  return <div style={{
    position: 'absolute',
    left: P.queue.x + 40,
    top: y,
    width: 170,
    height: 84,
    borderRadius: 20,
    background: '#1C1E22',
    border: `1.5px solid ${C.rule}`,
    boxShadow: '0 14px 34px rgba(0,0,0,.5)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    fontFamily,
    fontSize: 30,
    fontWeight: 500,
    letterSpacing: '.02em',
    color: C.muted,
    opacity,
  }}>
    <div style={{width: 10, height: 10, borderRadius: 5, background: C.hot, opacity: 0.7}} />
    job
  </div>;
};

export const Pilot34: React.FC = () => {
  const f = useCurrentFrame();
  const site = atState(f, WEBSITE);
  const browser = atState(f, BROWSER);
  const storage = atState(f, STORAGE);
  const queue = atState(f, QUEUE);
  const check = atState(f, CHECK);
  const gpu = atState(f, GPU);
  const push = 1 + 0.032 * easeInOut(f, 0, DURATION);

  return <AbsoluteFill style={{background: 'radial-gradient(1150px 780px at 50% 50%, #0B0C0F 0%, #060709 62%, #000 100%)'}}>
    <AbsoluteFill style={{transform: `scale(${push})`, transformOrigin: "50% 50%"}}>
      <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0}}>
        <Link path={PATHS.browserToWebsite} appear={100} />
        <Link path={PATHS.browserToStorage} appear={112} />
        <Link path={PATHS.websiteToQueue} appear={282} variant={f >= 433 ? 'dashed' : 'solid'} variantAt={433} gap={24 * ease(f, 435, 465)} />
        <Link path={PATHS.queueToCheck} appear={650} />
        <Link path={PATHS.checkToGpu} appear={698} />
        <g opacity={ease(f, 900, 915)}><Gate x={GATE_AT.x} y={GATE_AT.y} closed={ease(f, 936, 950)} cross={ease(f, 1016, 1024)} /></g>
      </svg>

      <Box label="Browser" x={P.browser.x} y={P.browser.y} appear={92} state={browser.state} stateAt={browser.at} prev={browser.prev} />
      <Box label="Website" x={P.website.x} y={P.website.y} appear={14} state={site.state} stateAt={site.at} prev={site.prev} />
      <Box label="Storage" service="S3" x={P.storage.x} y={P.storage.y} appear={106} state={storage.state} stateAt={storage.at} prev={storage.prev} />
      <Box label="Queue" service="SQS" x={P.queue.x} y={P.queue.y} appear={280} state={queue.state} stateAt={queue.at} prev={queue.prev} />
      <Box label="Check" service="Lambda" x={P.check.x} y={P.check.y} appear={646} state={check.state} stateAt={check.at} prev={check.prev} />
      <Box label="GPU" service="SageMaker" x={P.gpu.x} y={P.gpu.y} appear={690} state={gpu.state} stateAt={gpu.at} prev={gpu.prev} />

      <JobCard frame={f} />

      <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0}}>
        <Dot path={PATHS.browserToStorage} start={118} end={190} label="swee_choon.mp4" labelOffset={{x: -150, y: -60}} />
        <Dot path={PATHS.queueToCheck} start={662} end={724} />
        <Dot path={PATHS.checkToGpu} start={958} end={1016} color="#E05A4F" label="bad file" stopAt={0.58} labelOffset={{x: 0, y: -125}} linger />
      </svg>

      <Readout frame={f} />
      <Caption text="asynchronous" from={438} to={620} />
    </AbsoluteFill>
  </AbsoluteFill>;
};
