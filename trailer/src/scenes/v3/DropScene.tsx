import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {BrainHero} from '../BrainHero';
import {CUE} from '../../script';
import {cuts} from '../../beats';
import {Caption, Flash, Shade} from '../../edit/ui';
import {ease} from '../../edit/anim';

export const DropScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.drop;
  const push = ease(g, cuts.drop, cuts.hooked);
  return <AbsoluteFill style={{background: '#050607'}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + push * .04})`, transformOrigin: '50% 50%'}}>
      <BrainHero start={18} layout="solo" />
    </AbsoluteFill>
    <Shade top="rgba(7,8,10,.18)" bottom="rgba(7,8,10,.9)" />
    <Caption cue={CUE.drop} offset={cuts.drop} size={128} y={856} maxWidth={1700} />
    <Flash at={cuts.drop} offset={cuts.drop} strength={.55} length={12} />
  </AbsoluteFill>;
};
