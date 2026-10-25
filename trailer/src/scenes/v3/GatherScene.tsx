import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {BrainHero} from '../BrainHero';
import {CUE} from '../../script';
import {moments} from '../../beats';
import {ease} from '../../edit/anim';
import {brainAt} from './MinutesScene';

export const GatherScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + moments.silence;
  const shrink = ease(g, moments.silence, CUE.endingTitle.start);
  return <AbsoluteFill style={{background: '#050607'}}>
    <AbsoluteFill style={{transform: `scale(${1 - .3 * shrink})`}}>
      <BrainHero start={50} layout="solo" frameOffset={moments.silence - brainAt} />
    </AbsoluteFill>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 48%, transparent ${38 - 38 * shrink}%, rgba(5,6,8,${.25 + .7 * shrink}) ${52 + 30 * shrink}%, #050607 100%)`, pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
