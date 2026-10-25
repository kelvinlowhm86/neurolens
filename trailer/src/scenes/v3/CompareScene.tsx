import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import sintel from '../../sintel.json';
import {CUE, LABEL} from '../../script';
import {CLIPS, FPS, cuts, f} from '../../beats';
import {Graph, at, signed} from '../../edit/Graph';
import {clamp, ease, enter} from '../../edit/anim';
import {C, Caption, Flash, FullBleed} from '../../edit/ui';

const [A_FROM, A_TO] = CLIPS.compareA;
const [B_FROM, B_TO] = CLIPS.compareB;
const SPAN = A_TO - A_FROM;

const rangeOf = (): [number, number] => {
  const slice = [...sintel.signals.overall.slice(A_FROM, A_TO + 1), ...sintel.signals.overall.slice(B_FROM, B_TO + 1)];
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
  const s = (g - cuts.compare) / FPS;
  const progress = clamp(s / SPAN);
  const aAt = A_FROM + s;
  const bAt = B_FROM + s;
  const winFrame = cuts.compare + f(6);
  const win = ease(g, winFrame, winFrame + 8);
  const tag = enter(g, winFrame, 10);
  return <AbsoluteFill style={{background: C.bg}}>
    <div style={{position: 'absolute', left: 0, top: 0, width: 960, height: 1080, overflow: 'hidden', filter: `brightness(${1 - .4 * win})`}}>
      <FullBleed clip="sintel" start={A_FROM} />
      <div style={{position: 'absolute', left: 96, top: 310}}><Label name={LABEL.openingA} value={at(sintel.signals.overall, aAt)} color={C.cool} /></div>
    </div>
    <div style={{position: 'absolute', left: 960, top: 0, width: 960, height: 1080, overflow: 'hidden'}}>
      <FullBleed clip="sintel" start={B_FROM} />
      <div style={{position: 'absolute', right: 96, top: 310}}><Label name={LABEL.openingB} value={at(sintel.signals.overall, bAt)} color={C.hot} /></div>
      <AbsoluteFill style={{border: `6px solid ${C.hot}`, boxShadow: `inset 0 0 60px rgba(255,138,42,${.34 * win})`, opacity: win, pointerEvents: 'none'}} />
    </div>
    <div style={{position: 'absolute', left: 959, top: 0, width: 2, height: 1080, background: 'rgba(236,237,239,.32)', boxShadow: '0 0 24px #000'}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 560, background: 'linear-gradient(180deg, rgba(6,7,9,.7), transparent)'}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 580, bottom: 0, background: 'linear-gradient(180deg, transparent, rgba(6,7,9,.94) 34%)'}} />
    <Caption cue={CUE.compare} offset={cuts.compare} size={132} y={218} maxWidth={1700} />
    <div style={{position: 'absolute', left: 1440, top: 468, transform: `translate(-50%,-50%) scale(${tag})`, opacity: clamp(win * 3), padding: '14px 30px', borderRadius: 6, border: `3px solid ${C.hot}`, background: 'rgba(255,138,42,.13)', color: C.hot, fontSize: 48, fontWeight: 600, letterSpacing: '-.02em'}}>Wins</div>
    <div style={{position: 'absolute', left: 104, right: 104, bottom: 50}}>
      <Graph
        values={sintel.signals.overall}
        valuesOffset={A_FROM}
        comparison={sintel.signals.overall}
        comparisonOffset={B_FROM}
        from={0}
        to={SPAN}
        range={rangeOf()}
        width={1712}
        height={340}
        progress={progress}
        comparisonProgress={progress}
        playhead={s}
        zeroLabel={LABEL.typical}
        color={C.cool}
        comparisonColor={C.hot}
        comparisonStrokeWidth={5 + 4 * win}
        comparisonGlow={win}
        label="compare"
        background
      />
    </div>
    <Flash at={cuts.compare} offset={cuts.compare} strength={.3} length={8} color="#DCE8FF" />
  </AbsoluteFill>;
};
