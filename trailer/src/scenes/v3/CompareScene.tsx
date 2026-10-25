import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import bunny from '../../bunny.json';
import sintel from '../../sintel.json';
import {CUE, LABEL} from '../../script';
import {FPS, cuts, f} from '../../beats';
import {Graph, at, signed} from '../../edit/Graph';
import {clamp, ease, enter} from '../../edit/anim';
import {C, Caption, FullBleed} from '../../edit/ui';

const rangeOf = (): [number, number] => {
  const slice = [...bunny.signals.overall.slice(9, 18), ...sintel.signals.overall.slice(9, 18)];
  const low = Math.min(0, ...slice), high = Math.max(0, ...slice);
  const pad = (high - low) * .14;
  return [low - pad, high + pad];
};

const Label: React.FC<{name: string; value: number; color: string}> = ({name, value, color}) =>
  <div style={{fontSize: 44, display: 'flex', gap: 16, alignItems: 'center', color: C.text}}>
    <span style={{width: 40, height: 3, background: color, display: 'inline-block'}} />{name} <span style={{color, fontVariantNumeric: 'tabular-nums'}}>{signed(value)}</span>
  </div>;

export const CompareScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.compare;
  const t = 9 + (g - cuts.compare) / FPS;
  const progress = clamp((t - 9) / 8);
  const winFrame = cuts.compare + f(6);
  const win = ease(g, winFrame, winFrame + 8);
  const tag = enter(g, winFrame, 10);
  return <AbsoluteFill style={{background: C.bg}}>
    <div style={{position: 'absolute', left: 0, top: 0, width: 960, height: 1080, overflow: 'hidden', filter: `brightness(${1 - .4 * win})`}}>
      <FullBleed clip="bunny" start={9} />
      <div style={{position: 'absolute', left: 96, top: 310}}><Label name={LABEL.bunny} value={at(bunny.signals.overall, t)} color={C.hot} /></div>
    </div>
    <div style={{position: 'absolute', left: 960, top: 0, width: 960, height: 1080, overflow: 'hidden'}}>
      <FullBleed clip="sintel" start={9} />
      <div style={{position: 'absolute', right: 96, top: 310}}><Label name={LABEL.sintel} value={at(sintel.signals.overall, t)} color={C.cool} /></div>
      <AbsoluteFill style={{border: `6px solid ${C.hot}`, boxShadow: `inset 0 0 60px rgba(255,138,42,${.34 * win})`, opacity: win, pointerEvents: 'none'}} />
    </div>
    <div style={{position: 'absolute', left: 959, top: 0, width: 2, height: 1080, background: 'rgba(236,237,239,.32)', boxShadow: '0 0 24px #000'}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 560, background: 'linear-gradient(180deg, rgba(6,7,9,.7), transparent)'}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 580, bottom: 0, background: 'linear-gradient(180deg, transparent, rgba(6,7,9,.94) 34%)'}} />
    <Caption cue={CUE.compare} offset={cuts.compare} size={132} y={218} maxWidth={1700} />
    <div style={{position: 'absolute', left: 1440, top: 468, transform: `translate(-50%,-50%) scale(${tag})`, opacity: clamp(win * 3), padding: '14px 30px', borderRadius: 6, border: `3px solid ${C.hot}`, background: 'rgba(255,138,42,.13)', color: C.hot, fontSize: 48, fontWeight: 600, letterSpacing: '-.02em'}}>Wins</div>
    <div style={{position: 'absolute', left: 104, right: 104, bottom: 50}}>
      <Graph
        values={bunny.signals.overall}
        comparison={sintel.signals.overall}
        from={9}
        to={17}
        range={rangeOf()}
        width={1712}
        height={340}
        progress={progress}
        comparisonProgress={progress}
        playhead={t}
        zeroLabel={LABEL.typical}
        comparisonStrokeWidth={5 + 4 * win}
        comparisonGlow={win}
        label="compare"
        background
      />
    </div>
  </AbsoluteFill>;
};
