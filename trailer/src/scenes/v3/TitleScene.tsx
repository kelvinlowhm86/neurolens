import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {CUE} from '../../script';
import {cuts, f, moments} from '../../beats';
import {clamp, ease} from '../../edit/anim';
import {C, Caption, Flash} from '../../edit/ui';

const LETTERS = CUE.endingTitle.text.split('');
const SWEEP = f(.6);
const GLOW_WIDTH = .22;
const WHITE: readonly [number, number, number] = [236, 237, 239];
const HOT: readonly [number, number, number] = [255, 138, 42];
const mix = (from: readonly [number, number, number], to: readonly [number, number, number], t: number) => {
  const c = from.map((value, i) => Math.round(value + (to[i] - value) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
};

export const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.title;
  const out = 1 - ease(g, cuts.credits - 8, cuts.credits);
  const head = (g - moments.finalHit - 1) / SWEEP;
  return <AbsoluteFill style={{background: '#050607'}}>
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 46%, rgba(255,138,42,.07), transparent 48%), radial-gradient(ellipse at 50% 50%, rgba(111,126,153,.12), transparent 64%)'}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 430, textAlign: 'center', opacity: out, transform: 'translateY(-50%)'}}>
      <div style={{fontSize: 238, lineHeight: 1, fontWeight: 600, letterSpacing: '-.07em', color: C.text}}>
        {LETTERS.map((letter, i) => {
          const x = LETTERS.length > 1 ? i / (LETTERS.length - 1) : 0;
          const w = g - moments.finalHit < 1 ? 0 : clamp(1 - Math.abs(head - x) / GLOW_WIDTH);
          const glow = w * w * (3 - 2 * w);
          const shadow = `0 24px 90px rgba(0,0,0,.8), 0 0 90px rgba(255,138,42,${.16 + .5 * glow}), 0 0 ${70 * glow}px rgba(255,170,90,${.65 * glow})`;
          return <span key={`${letter}-${i}`} style={{color: mix(WHITE, HOT, glow), textShadow: shadow}}>{letter}</span>;
        })}
      </div>
    </div>
    <Caption cue={CUE.endingSub} offset={cuts.title} size={57} y={640} maxWidth={1600} weight={400} color={C.muted} letterSpacing={-.025} loosen={3} />
    <Flash at={moments.finalHit} offset={cuts.title} strength={.5} length={9} />
  </AbsoluteFill>;
};
