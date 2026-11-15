import React from 'react';
import {AbsoluteFill, Easing, OffthreadVideo, interpolate, staticFile, useCurrentFrame} from 'remotion';
import swee from '../../public/walkthrough/swee/result.json';
import {Graph} from '../edit/Graph';
import {C, fontFamily} from '../edit/ui';
import {FPS, SWEE} from './timeline';

const overall = swee.signals.overall as number[];
const low = Math.min(...overall);
const high = Math.max(...overall);
const pad = (high - low) * 0.12 || 0.04;
const RANGE: [number, number] = [low - pad, high + pad];

export const Opening: React.FC = () => {
  const frame = useCurrentFrame();
  const draw = interpolate(frame, [FPS * 0.7, FPS * 3.4], [0.04, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

  return <AbsoluteFill style={{background: '#000', fontFamily, color: C.text, overflow: 'hidden'}}>
    <OffthreadVideo
      src={staticFile(SWEE.video)}
      muted
      style={{
        position: 'absolute', left: 0, top: 0, width: '100%', height: '100%',
        objectFit: 'cover', filter: 'blur(38px) brightness(.34) saturate(1.1)',
        transform: 'scale(1.08)',
      }}
    />
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 42%, rgba(8,9,11,.10) 0%, rgba(0,0,0,.72) 68%, #000 100%)'}} />

    <div style={{
      position: 'absolute', left: 120, top: 84, fontSize: 18, fontWeight: 500,
      letterSpacing: '.14em', textTransform: 'uppercase', color: C.muted,
    }}>Swee Choon · TikTok · 0:44</div>

    <div style={{
      position: 'absolute', left: 746, top: 50, width: 428, height: 760,
      borderRadius: 18, overflow: 'hidden', background: '#000',
      boxShadow: '0 40px 120px rgba(0,0,0,.9), 0 0 0 1px rgba(255,255,255,.09), 0 0 80px rgba(255,138,42,.12)',
    }}>
      <OffthreadVideo
        src={staticFile(SWEE.video)}
        muted
        style={{width: '100%', height: '100%', objectFit: 'cover'}}
      />
    </div>

    <div style={{position: 'absolute', left: 210, top: 842, width: 1500}}>
      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8,
      }}>
        <div style={{fontSize: 20, fontWeight: 500, color: C.muted, letterSpacing: '-0.005em'}}>
          Real response, second by second
        </div>
        <div style={{fontSize: 18, fontWeight: 500, color: C.muted, fontVariantNumeric: 'tabular-nums'}}>
          0:00 – 0:44
        </div>
      </div>
      <div style={{
        position: 'relative', height: 200, borderRadius: 14,
        background: 'linear-gradient(180deg, rgba(23,24,27,.86), rgba(9,10,12,.92))',
        border: `1px solid ${C.rule}`, overflow: 'hidden',
        boxShadow: '0 24px 70px rgba(0,0,0,.6)',
      }}>
        <div style={{position: 'absolute', left: 18, right: 18, top: 22, bottom: 10}}>
          <Graph
            values={overall}
            from={0}
            to={overall.length - 1}
            range={RANGE}
            width={1464}
            height={168}
            progress={draw}
            color={C.hot}
            axes={false}
            label="swee-overall"
            strokeWidth={4}
          />
        </div>
        <div style={{
          position: 'absolute', left: 22, right: 22, bottom: 14,
          display: 'flex', justifyContent: 'space-between', fontSize: 16,
          color: 'rgba(160,166,175,.75)', fontVariantNumeric: 'tabular-nums',
        }}>
          <span>0:00</span><span>0:10</span><span>0:20</span><span>0:30</span><span>0:44</span>
        </div>
      </div>
    </div>

    <div style={{
      position: 'absolute', right: 120, top: 84, textAlign: 'right',
      fontSize: 18, fontWeight: 500, letterSpacing: '-0.005em', color: C.muted,
    }}>
      tiktok_ZSg5soxrR
    </div>
  </AbsoluteFill>;
};
