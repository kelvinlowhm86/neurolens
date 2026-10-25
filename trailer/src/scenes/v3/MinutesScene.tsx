import React from 'react';
import {AbsoluteFill, Img, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {BrainHero} from '../BrainHero';
import {CUE} from '../../script';
import {cuts, f, moments} from '../../beats';
import {clamp, ease, enter} from '../../edit/anim';
import {C, Flash} from '../../edit/ui';

const brainAt = cuts.minutes + f(3);
const cardW = 1100, cardH = 560;
const ringR = 80, ringC = 2 * Math.PI * ringR;

export const MinutesScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.minutes;
  const move = ease(g, cuts.minutes, cuts.minutes + f(.6));
  const wordSize = 200 + (120 - 200) * move;
  const wordY = 540 + (150 - 540) * move;
  const wordIn = enter(g, cuts.minutes, 16);
  const wordOut = 1 - ease(g, CUE.minutes.end - f(.25), CUE.minutes.end);
  const cardIn = ease(g, cuts.minutes, cuts.minutes + 5);
  const drop = enter(g, cuts.minutes, 20);
  const ring = ease(g, cuts.minutes + f(.5), brainAt);
  const toBrain = ease(g, brainAt, brainAt + f(.45));
  const fade = clamp((g - (moments.silence - f(.3))) / (f(.3) - 1));
  return <AbsoluteFill style={{background: '#0B0C0E'}}>
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 40%, rgba(255,138,42,.12), transparent 52%), radial-gradient(ellipse at 76% 64%, rgba(111,126,153,.14), transparent 46%)'}} />
    <AbsoluteFill style={{opacity: toBrain, transform: `scale(${.97 + .03 * toBrain})`}}>
      <Sequence from={brainAt - cuts.minutes}>
        <BrainHero start={50} layout="solo" />
      </Sequence>
    </AbsoluteFill>
    <div style={{position: 'absolute', left: (1920 - cardW) / 2, top: 320, width: cardW, height: cardH, borderRadius: 16, background: C.surface, border: '1px solid rgba(236,237,239,.1)', boxShadow: '0 42px 120px rgba(0,0,0,.62)', opacity: cardIn * (1 - toBrain), transform: `scale(${1 - .04 * toBrain})`, filter: `blur(${toBrain * 8}px)`}}>
      <div style={{position: 'absolute', left: 46, top: 46, width: 590, height: 332, borderRadius: 10, overflow: 'hidden', transform: `translateY(${(1 - drop) * -90}px) scale(${.94 + .06 * drop})`, opacity: clamp(drop * 2)}}>
        <Img src={staticFile('bbb_720p_55s/thumb.jpg')} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
        <AbsoluteFill style={{background: 'linear-gradient(180deg, transparent 58%, rgba(0,0,0,.5))'}} />
      </div>
      <div style={{position: 'absolute', left: 46, top: 402, fontSize: 40, lineHeight: 1, letterSpacing: '-.02em', color: C.muted}}>Big Buck Bunny</div>
      <div style={{position: 'absolute', left: 760, top: 160, width: 180, height: 180}}>
        <svg width={180} height={180} viewBox="0 0 180 180">
          <circle cx="90" cy="90" r={ringR} fill="none" stroke="rgba(236,237,239,.12)" strokeWidth="14" />
          <circle cx="90" cy="90" r={ringR} fill="none" stroke={C.hot} strokeWidth="14" strokeLinecap="round" strokeDasharray={ringC} strokeDashoffset={ringC * (1 - ring)} transform="rotate(-90 90 90)" />
        </svg>
        <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 48, fontWeight: 600, fontVariantNumeric: 'tabular-nums'}}>{Math.round(ring * 100)}%</div>
      </div>
      <div style={{position: 'absolute', left: 650, top: 372, width: 440, textAlign: 'center', fontSize: 40, lineHeight: 1, color: C.muted, letterSpacing: '-.02em'}}>Predicted response</div>
    </div>
    <div style={{position: 'absolute', left: 0, right: 0, top: wordY, transform: `translateY(-50%) scale(${.94 + wordIn * .06})`, textAlign: 'center', opacity: wordOut}}>
      <div style={{fontSize: wordSize, lineHeight: 1, fontWeight: 600, letterSpacing: '-.065em', color: C.hot, textShadow: '0 24px 90px rgba(0,0,0,.7), 0 0 70px rgba(255,138,42,.25)'}}>{CUE.minutes.text}</div>
    </div>
    <Flash at={cuts.minutes} offset={cuts.minutes} strength={.3} length={8} />
    <AbsoluteFill style={{background: '#050607', opacity: fade, pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
