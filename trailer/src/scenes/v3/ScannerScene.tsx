import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {Scanner} from '../Scanner';
import {CUE} from '../../script';
import {cuts, f, moments} from '../../beats';
import {C, Caption, Flash, Shade} from '../../edit/ui';
import {beatPulse, clamp, ease} from '../../edit/anim';

const span = (a: number, b: number) => Math.max(1, b - a);
const SLOW_PUSH = .65;

export const ScannerScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.scanner;
  const cssPush = clamp((g - cuts.scanner) / span(cuts.scanner, cuts.question));
  const slow = SLOW_PUSH * cssPush;
  const fly = clamp((g - cuts.question) / span(cuts.question, moments.stop));
  const beat = beatPulse(g);
  const k = g - moments.bigHit;
  const shake = k >= 0 && k < 10 ? 1 - k / 10 : 0;
  const sx = (k % 2 ? -1 : 1) * 14 * shake;
  const sy = (k % 2 ? 1 : -1) * 9 * shake;
  const glow = ease(g, moments.bigHit, CUE.stopA.start) * .35;
  const buildFlash = Math.max(...CUE.build.map((cue) => clamp(1 - (g - cue.start) / 7)));
  const whoosh = ease(g, moments.stop - f(.3), moments.stop);
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{transform: `scale(${1.025 + cssPush * .1 + beat * .004 * (1 - fly)}) translate(${sx}px, ${cssPush * -12 + sy}px)`, transformOrigin: '52% 52%'}}>
      <Scanner cameraProgress={slow} flyProgress={fly} />
    </AbsoluteFill>
    <Shade top="rgba(7,8,10,.24)" bottom="rgba(7,8,10,.78)" />
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 53%, transparent 28%, rgba(5,6,8,.42) 100%)'}} />
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 51%, rgba(255,138,42,${glow}), transparent 46%)`, mixBlendMode: 'screen', pointerEvents: 'none'}} />
    <Caption cue={CUE.answer} offset={cuts.scanner} size={96} y={505} maxWidth={1500} />
    {CUE.build.map((cue) => <Caption key={cue.id} cue={cue} offset={cuts.scanner} size={184} y={540} maxWidth={1760} outFrames={1} />)}
    <Caption cue={CUE.stopA} offset={cuts.scanner} size={132} y={480} maxWidth={1520} outFrames={1} loosen={0} />
    <AbsoluteFill style={{background: '#FFFFFF', opacity: buildFlash * .12, mixBlendMode: 'screen', pointerEvents: 'none'}} />
    <AbsoluteFill style={{background: '#FFFFFF', opacity: whoosh * .5, mixBlendMode: 'screen', pointerEvents: 'none'}} />
    <Flash at={cuts.scanner} offset={cuts.scanner} strength={.18} length={10} />
    <Flash at={moments.bigHit} offset={cuts.scanner} strength={.3} length={10} />
  </AbsoluteFill>;
};
