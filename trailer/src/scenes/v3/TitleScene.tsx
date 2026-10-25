import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {CUE} from '../../script';
import {cuts, f, moments} from '../../beats';
import {ease} from '../../edit/anim';
import {C, Caption, Flash} from '../../edit/ui';

// Warm glow on the hit, easing back to plain white text.
const GLOW = f(1.1);

export const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.title;
  const out = 1 - ease(g, cuts.credits - 8, cuts.credits);
  const glow = g < moments.finalHit ? 0 : 1 - ease(g, moments.finalHit, moments.finalHit + GLOW);
  return <AbsoluteFill style={{background: '#050607'}}>
    <div style={{position: 'absolute', left: 0, right: 0, top: 430, textAlign: 'center', opacity: out, transform: 'translateY(-50%)'}}>
      <div style={{fontSize: 238, lineHeight: 1, fontWeight: 600, letterSpacing: '-.07em', color: C.text,
        textShadow: `0 24px 90px rgba(0,0,0,.8), 0 0 90px rgba(255,138,42,${.45 * glow}), 0 0 ${40 + 60 * glow}px rgba(255,170,90,${.55 * glow})`}}>
        {CUE.endingTitle.text}
      </div>
    </div>
    <Caption cue={CUE.endingSub} offset={cuts.title} size={57} y={640} maxWidth={1600} weight={400} color={C.muted} letterSpacing={-.025} loosen={3} />
    <Flash at={moments.finalHit} offset={cuts.title} strength={.5} length={9} />
  </AbsoluteFill>;
};
