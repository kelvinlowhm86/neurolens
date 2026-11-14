import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {FPS} from './timeline';

const ROWS = [
  'Uploaded',
  'Waiting for the GPU',
  'Listening for speech',
  'Predicting with sound',
  'Predicting on mute',
  'Reading brain areas',
  'Saving the shared copy',
  'Saving the result',
];

type Status = 'done' | 'active' | 'pending';

const statusAt = (i: number, t: number): Status => {
  if (i === 0) return 'done';
  if (i === 1) return t < 1.6 ? 'active' : 'done';
  const step = (4.4 - 1.6) / 6;
  const start = 1.6 + (i - 2) * step;
  if (t < start) return 'pending';
  if (t < start + step) return 'active';
  return 'done';
};

const Dot: React.FC<{status: Status; t: number}> = ({status, t}) => {
  if (status === 'done') return <div style={{
    width: 15, height: 15, borderRadius: 999, background: C.hot,
  }} />;
  if (status === 'active') {
    const pulse = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 1.2);
    return <div style={{
      width: 15, height: 15, borderRadius: 999, boxSizing: 'border-box',
      border: `3px solid ${C.hot}`, background: 'transparent',
      boxShadow: `0 0 0 ${4 + pulse * 5}px rgba(255,138,42,${.10 + pulse * .06})`,
    }} />;
  }
  return <div style={{width: 15, height: 15, borderRadius: 999, background: 'rgba(160,166,175,.32)'}} />;
};

export const Steps: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;

  return <AbsoluteFill style={{background: '#000', fontFamily, color: C.text}}>
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <div style={{
        width: 900, boxSizing: 'border-box', padding: '48px 52px', transform: 'scale(1.3)',
        background: C.surface, border: `1px solid ${C.rule}`, borderRadius: 14,
        boxShadow: '0 30px 90px rgba(0,0,0,.6)',
      }}>
        <div style={{fontSize: 20, color: C.muted, letterSpacing: '.02em', marginBottom: 8}}>Analysing</div>
        <div style={{fontSize: 40, fontWeight: 600, letterSpacing: '-0.03em'}}>big_buck_bunny.mp4</div>

        <div style={{position: 'relative', marginTop: 36}}>
          <div style={{position: 'absolute', left: 7, top: 10, bottom: 10, width: 1, background: C.rule}} />
          {ROWS.map((label, i) => {
            const status = statusAt(i, t);
            return <div key={label} style={{
              height: 64, display: 'flex', alignItems: 'center', position: 'relative',
            }}>
              <div style={{width: 15, display: 'flex', justifyContent: 'center'}}>
                <Dot status={status} t={t} />
              </div>
              <div style={{
                marginLeft: 24, fontSize: 30, letterSpacing: '-0.02em',
                color: status === 'pending' ? C.muted : C.text,
                opacity: status === 'done' ? .8 : status === 'active' ? 1 : .5,
              }}>{label}</div>
            </div>;
          })}
        </div>
      </div>
    </AbsoluteFill>
  </AbsoluteFill>;
};
