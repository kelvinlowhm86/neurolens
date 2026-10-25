import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {CUE} from '../../script';
import {cuts, moments} from '../../beats';
import {beatPulse, ease} from '../../edit/anim';
import {C, Caption, Flash} from '../../edit/ui';

export const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.title;
  const slam = ease(g, CUE.endingTitle.start, CUE.endingTitle.start + 12);
  const out = 1 - ease(g, cuts.credits - 8, cuts.credits);
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: '#050607'}}>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 46%, rgba(255,138,42,${.07 + beat * .03}), transparent 48%), radial-gradient(ellipse at 50% 50%, rgba(111,126,153,.12), transparent 64%)`}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 430, textAlign: 'center', opacity: g >= CUE.endingTitle.start ? out : 0, transform: `translateY(-50%) scale(${1.28 - slam * .28 + beat * .007})`, filter: `blur(${(1 - slam) * 11}px)`}}>
      <div style={{fontSize: 238, lineHeight: 1, fontWeight: 600, letterSpacing: '-.07em', color: C.text, textShadow: '0 24px 90px rgba(0,0,0,.8), 0 0 90px rgba(255,138,42,.16)'}}>{CUE.endingTitle.text}</div>
    </div>
    <Caption cue={CUE.endingSub} offset={cuts.title} size={57} y={640} maxWidth={1600} weight={400} color={C.muted} letterSpacing={-.025} loosen={3} />
    <Flash at={moments.finalHit} offset={cuts.title} strength={.5} length={13} />
  </AbsoluteFill>;
};
