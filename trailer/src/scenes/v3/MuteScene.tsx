import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import bunny from '../../bunny.json';
import {CUE, LABEL} from '../../script';
import {CLIPS, FPS, cuts, f, moments} from '../../beats';
import {Graph, at, signed} from '../../edit/Graph';
import {clamp, ease, enter} from '../../edit/anim';
import {C, Caption, Flash, FullBleed, Shade} from '../../edit/ui';

const [MUTE_FROM, MUTE_TO] = CLIPS.mute;

const rangeOf = (): [number, number] => {
  const slice = [...bunny.signals.overall.slice(MUTE_FROM, MUTE_TO + 1), ...bunny.muted.overall.slice(MUTE_FROM, MUTE_TO + 1)];
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
  const t = MUTE_FROM + (g - cuts.mute) / FPS;
  const progress = clamp((t - MUTE_FROM) / (MUTE_TO - MUTE_FROM));
  const shrink = ease(g, cuts.mute + f(.4), cuts.mute + f(1));
  const pop = enter(g, cuts.mute, 11.5);
  const x = 960 + (1832 - 960) * shrink;
  const y = 540 + (94 - 540) * shrink;
  const size = 160 + (64 - 160) * shrink;
  const questionOut = 1 - ease(g, moments.muteStop, moments.muteStop + f(.3));
  const scrim = ease(g, moments.muteStop, moments.muteStop + f(.2)) * .62;
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill>
      <FullBleed clip="bunny" start={MUTE_FROM} />
    </AbsoluteFill>
    <Shade top="rgba(6,7,9,.52)" bottom="rgba(6,7,9,.97)" />
    <AbsoluteFill style={{opacity: questionOut}}>
      <Caption cue={CUE.mute} offset={cuts.mute} size={126} y={244} maxWidth={1740} />
    </AbsoluteFill>
    <div style={{position: 'absolute', left: 150, top: 430, display: 'flex', gap: 62, alignItems: 'baseline'}}>
      <div style={{fontSize: 40, color: C.text, display: 'flex', gap: 16, alignItems: 'center'}}>
        <span style={{width: 40, height: 3, background: C.hot, display: 'inline-block'}} />{LABEL.withAudio} <span style={{color: C.hot, fontVariantNumeric: 'tabular-nums'}}>{signed(at(bunny.signals.overall, t))}</span>
      </div>
      <div style={{fontSize: 40, color: C.muted, display: 'flex', gap: 16, alignItems: 'center'}}>
        <span style={{width: 40, height: 0, borderTop: `3px dashed ${C.cool}`, display: 'inline-block'}} />{LABEL.mutedLabel} <span style={{color: C.cool, fontVariantNumeric: 'tabular-nums'}}>{signed(at(bunny.muted.overall, t))}</span>
      </div>
    </div>
    <div style={{position: 'absolute', left: 104, right: 104, top: 520}}>
      <Graph
        values={bunny.signals.overall}
        comparison={bunny.muted.overall}
        from={MUTE_FROM}
        to={MUTE_TO}
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
    <AbsoluteFill style={{background: `rgba(4,5,7,${scrim})`, pointerEvents: 'none'}} />
    <div style={{position: 'absolute', left: x, top: y, width: size, height: size, transform: `translate(-50%,-50%) scale(${pop})`, borderRadius: '50%', background: 'rgba(7,8,10,.58)', boxShadow: '0 14px 54px rgba(0,0,0,.62)', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
      <MuteIcon size={size * .62} />
    </div>
    <Caption cue={CUE.muteAnswer} offset={cuts.mute} size={148} y={540} maxWidth={1520} outFrames={1} loosen={0} />
    <Flash at={cuts.mute} offset={cuts.mute} strength={.18} length={7} color="#DCE8FF" />
  </AbsoluteFill>;
};
