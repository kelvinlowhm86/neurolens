import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame} from 'remotion';
import {Scanner} from '../Scanner';
import {CUE} from '../../script';
import {FPS, beats, cuts, f, moments} from '../../beats';
import {ease} from '../../edit/anim';
import {C, Caption, Shade} from '../../edit/ui';

export const WeeksAgainScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.weeksAgain;
  const ticks = beats.filter((b) => b.seconds >= cuts.weeksAgain / FPS && b.seconds < cuts.minutes / FPS);
  const passed = ticks.filter((b) => b.seconds <= g / FPS).length;
  const day = Math.min(21, 1 + Math.round((passed / ticks.length) * 20));
  const black = ease(g, moments.weeksStop, moments.weeksStop + f(.35));
  const scribble = ease(g, moments.weeksStop, moments.weeksStop + f(.45));
  return <AbsoluteFill style={{background: '#000'}}>
    <Sequence durationInFrames={cuts.minutes - cuts.weeksAgain} playbackRate={.5}>
      <AbsoluteFill style={{filter: 'grayscale(1) brightness(.6)'}}><Scanner /></AbsoluteFill>
    </Sequence>
    <Shade top="rgba(6,7,9,.24)" bottom="rgba(6,7,9,.86)" />
    <div style={{position: 'absolute', left: 96, top: 68, fontSize: 40, lineHeight: 1, letterSpacing: '.01em', color: C.muted, fontVariantNumeric: 'tabular-nums'}}>Day {day}</div>
    <AbsoluteFill style={{background: '#000', opacity: black}} />
    <Caption cue={CUE.weeks} offset={cuts.weeksAgain} size={200} y={540} maxWidth={1760} outFrames={1} loosen={0} />
    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, pointerEvents: 'none', filter: 'drop-shadow(0 0 12px rgba(255,138,42,.55))'}}>
      <path
        d="M 574 548 C 690 606, 812 512, 950 590 C 1090 670, 1210 524, 1348 588 M 602 620 C 736 578, 858 658, 1004 606 C 1140 558, 1268 646, 1382 596"
        fill="none"
        stroke={C.hot}
        strokeWidth="14"
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        strokeDasharray="1"
        strokeDashoffset={1 - scribble}
      />
    </svg>
  </AbsoluteFill>;
};
