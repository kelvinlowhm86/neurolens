import React from 'react';
import {AbsoluteFill, Freeze, useCurrentFrame} from 'remotion';
import sintel from '../../sintel.json';
import {CUE, LABEL} from '../../script';
import {FPS, cuts, f, moments} from '../../beats';
import {Graph, at, signed} from '../../edit/Graph';
import {clamp, ease, enter} from '../../edit/anim';
import {C, Caption, Flash, FullBleed, Shade} from '../../edit/ui';

const rangeOf = (): [number, number] => {
  const slice = [...sintel.signals.overall.slice(19, 26), ...sintel.muted.overall.slice(19, 26)];
  const low = Math.min(0, ...slice), high = Math.max(0, ...slice);
  const pad = (high - low) * .18 || .04;
  return [low - pad, high + pad];
};

const MuteIcon: React.FC<{size: number}> = ({size}) =>
  <svg width={size} height={size} viewBox="0 0 64 64" style={{display: 'block'}}>
    <path d="M10 25h11L38 11v42L21 39H10z" fill="#FFFFFF" />
    <line x1="46" y1="24" x2="59" y2="40" stroke="#FFFFFF" strokeWidth="5" strokeLinecap="round" />
  </svg>;

export const MuteScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.mute;
  const local = g - cuts.mute;
  const holdFrom = moments.muteStop - cuts.mute, holdTo = moments.muteBack - cuts.mute;
  const effective = local >= holdTo ? holdFrom + (local - holdTo) : Math.min(local, holdFrom);
  const t = 19 + effective / FPS;
  const progress = clamp((t - 19) / 6);
  const shrink = ease(g, cuts.mute + f(.4), cuts.mute + f(1));
  const pop = enter(g, cuts.mute, 9);
  const x = 960 + (1832 - 960) * shrink;
  const y = 540 + (94 - 540) * shrink;
  const size = 160 + (64 - 160) * shrink;
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill>
      <Freeze frame={effective}><FullBleed clip="sintel" start={19} /></Freeze>
    </AbsoluteFill>
    <Shade top="rgba(6,7,9,.52)" bottom="rgba(6,7,9,.97)" />
    <Caption cue={CUE.mute} offset={cuts.mute} size={126} y={244} maxWidth={1740} />
    <div style={{position: 'absolute', left: 150, top: 430, display: 'flex', gap: 62, alignItems: 'baseline'}}>
      <div style={{fontSize: 40, color: C.text, display: 'flex', gap: 16, alignItems: 'center'}}>
        <span style={{width: 40, height: 3, background: C.hot, display: 'inline-block'}} />{LABEL.withAudio} <span style={{color: C.hot, fontVariantNumeric: 'tabular-nums'}}>{signed(at(sintel.signals.overall, t))}</span>
      </div>
      <div style={{fontSize: 40, color: C.muted, display: 'flex', gap: 16, alignItems: 'center'}}>
        <span style={{width: 40, height: 0, borderTop: `3px dashed ${C.cool}`, display: 'inline-block'}} />{LABEL.mutedLabel} <span style={{color: C.cool, fontVariantNumeric: 'tabular-nums'}}>{signed(at(sintel.muted.overall, t))}</span>
      </div>
    </div>
    <div style={{position: 'absolute', left: 104, right: 104, top: 520}}>
      <Graph
        values={sintel.signals.overall}
        comparison={sintel.muted.overall}
        from={19}
        to={25}
        range={rangeOf()}
        width={1712}
        height={420}
        progress={progress}
        comparisonProgress={progress}
        comparisonDashed
        playhead={t}
        zeroLabel={LABEL.typical}
        label="mute"
        background
      />
    </div>
    <div style={{position: 'absolute', left: x, top: y, width: size, height: size, transform: `translate(-50%,-50%) scale(${pop})`, borderRadius: '50%', background: 'rgba(7,8,10,.58)', boxShadow: '0 14px 54px rgba(0,0,0,.62)', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
      <MuteIcon size={size * .62} />
    </div>
    <Flash at={cuts.mute} offset={cuts.mute} strength={.18} length={7} color="#DCE8FF" />
  </AbsoluteFill>;
};
