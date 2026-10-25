import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {CREDIT_LINES, CUE} from '../../script';
import {cuts, f} from '../../beats';
import {ease} from '../../edit/anim';
import {C} from '../../edit/ui';

export const CreditsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.credits;
  const fadeIn = ease(g, CUE.credits.start + f(.3), CUE.credits.start + f(1));
  const black = ease(g, cuts.end - 16, cuts.end - 1);
  return <AbsoluteFill style={{background: '#050607'}}>
    <div style={{position: 'absolute', left: 250, right: 250, top: 486, textAlign: 'center', fontSize: 24, lineHeight: 1.9, letterSpacing: '.01em', color: C.muted, opacity: fadeIn}}>
      {CREDIT_LINES.map((line) => <div key={line}>{line}</div>)}
    </div>
    <AbsoluteFill style={{background: '#000', opacity: black, pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
