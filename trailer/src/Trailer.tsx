import React from 'react';
import {
  AbsoluteFill, Audio, Sequence,
  interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import bunny from './bunny.json';
import sintel from './sintel.json';
import {Scanner} from './scenes/Scanner';
import {BrainHero} from './scenes/BrainHero';
import {CUE, CREDIT_LINES, LABEL, type Cue} from './script';
import {DURATION, FPS, cuts, f, moments, SFX} from './beats';
import {Graph, at, signed} from './edit/Graph';
import {beatPulse, clamp, ease, enter} from './edit/anim';
import {C, Caption, Flash, FullBleed, Shade, fontFamily} from './edit/ui';
import {OpenScene} from './scenes/v3/OpenScene';
import {ScannerScene} from './scenes/v3/ScannerScene';
import {StopScene} from './scenes/v3/StopScene';
import {DropScene} from './scenes/v3/DropScene';
import {HookedScene, DriftScene} from './scenes/v3/ClipChartScene';

const both = [...bunny.signals.overall, ...sintel.signals.overall];
const bothLow = Math.min(...both), bothHigh = Math.max(...both);
const bothPad = (bothHigh - bothLow) * .14;
const bothRange: [number, number] = [bothLow - bothPad, bothHigh + bothPad];
const signalArrays = LABEL.signals.map((name) => bunny.signals[name.toLowerCase() as keyof typeof bunny.signals] as number[]);

const span = (a: number, b: number) => Math.max(1, b - a);

const signalPositions = [
  [350, 222], [852, 158], [1470, 246], [1490, 702], [966, 872], [372, 848], [178, 512],
];

const SignalWord: React.FC<{cue: Cue; index: number; offset: number}> = ({cue, index, offset}) => {
  const f = useCurrentFrame();
  const g = f + offset;
  const p = enter(g, cue.start, 15);
  const beat = beatPulse(g) * clamp((g - cue.start) / 2);
  const [x, y] = signalPositions[index];
  const value = at(signalArrays[index], 25 + index);
  const drift = Math.sin((g - cue.start) / 38 + index) * 3;
  return <div style={{position: 'absolute', left: x - 230, top: y - 46 + drift, width: 460, textAlign: 'center', opacity: clamp((g - cue.start) / 4) * (1 - ease(g, cuts.mute - 4, cuts.mute)), transform: `scale(${.62 + p * .38 + beat * .05})`, filter: `blur(${(1 - clamp(p)) * 8}px)`, pointerEvents: 'none'}}>
    <div style={{fontSize: 78, lineHeight: 1, fontWeight: 600, letterSpacing: '-.055em', textShadow: '0 8px 38px #000'}}>{cue.text}</div>
    <div style={{marginTop: 10, fontSize: 28, color: C.hot, fontVariantNumeric: 'tabular-nums', textShadow: '0 4px 18px #000'}}>{signed(value)}</div>
    <div style={{margin: '12px auto 0', width: 46 + beat * 38, height: 2, background: C.cool, opacity: .45 + beat * .4, borderRadius: 2}} />
  </div>;
};

const SignalsScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.signals;
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 50%, rgba(255,138,42,${.035 + beatPulse(g) * .02}), transparent 54%), radial-gradient(ellipse at 18% 16%, rgba(111,126,153,.15), transparent 40%), radial-gradient(ellipse at 84% 82%, rgba(111,126,153,.12), transparent 42%)`}} />
    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, opacity: .2}}>
      {signalPositions.map(([x, y], i) => <line key={i} x1={960} y1={540} x2={x} y2={y} stroke={C.cool} strokeWidth="1" strokeDasharray="2 9" />)}
      <circle cx={960} cy={540} r={220 + beatPulse(g) * 8} fill="none" stroke={C.rule} strokeWidth="1" />
      <circle cx={960} cy={540} r={390 + beatPulse(g) * 12} fill="none" stroke={C.rule} strokeWidth="1" strokeOpacity=".7" />
    </svg>
    {CUE.signalNames.map((cue, i) => <SignalWord key={cue.id} cue={cue} index={i} offset={cuts.signals} />)}
    <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 3, background: `linear-gradient(90deg, transparent, rgba(255,138,42,${.2 + beatPulse(g) * .4}), transparent)`}} />
  </AbsoluteFill>;
};

const MutedScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.mute;
  const now = 25 + (g - cuts.mute) / FPS;
  const split = ease(g, cuts.mute, cuts.mute + 18);
  const duck = Math.max(0, 1 - Math.abs(g - cuts.mute) / 4);
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + duck * .012})`}}>
      <FullBleed clip="bunny" start={25} blur={duck * 1.2} />
    </AbsoluteFill>
    <Shade top="rgba(6,7,9,.62)" bottom="rgba(6,7,9,.96)" />
    <Caption cue={CUE.mute} offset={cuts.mute} size={126} y={244} maxWidth={1740} />
    <div style={{position: 'absolute', left: 150, top: 414, display: 'flex', gap: 62, alignItems: 'baseline'}}>
      <div style={{fontSize: 26, color: C.muted, display: 'flex', gap: 14, alignItems: 'center'}}>
        <span style={{width: 34, height: 3, background: C.hot, display: 'inline-block'}} />{LABEL.withAudio} <span style={{color: C.hot, fontVariantNumeric: 'tabular-nums'}}>{signed(at(bunny.signals.overall, now))}</span>
      </div>
      <div style={{fontSize: 26, color: C.muted, display: 'flex', gap: 14, alignItems: 'center', opacity: split}}>
        <span style={{width: 34, height: 0, borderTop: `3px dashed ${C.cool}`, display: 'inline-block'}} />{LABEL.mutedLabel} <span style={{color: C.cool, fontVariantNumeric: 'tabular-nums'}}>{signed(at(bunny.muted.overall, now))}</span>
      </div>
    </div>
    <div style={{position: 'absolute', left: 104, right: 104, top: 506}}>
      <Graph
        values={bunny.signals.overall}
        comparison={bunny.muted.overall}
        from={20}
        to={36}
        range={[-.15, .28]}
        width={1712}
        height={446}
        comparisonDashed
        comparisonProgress={split}
        marker={{second: now, label: signed(at(bunny.signals.overall, now))}}
        comparisonMarker={{second: now, label: signed(at(bunny.muted.overall, now))}}
        label="muted"
        background
      />
    </div>
    <Flash at={cuts.mute} offset={cuts.mute} strength={.18} length={7} color="#DCE8FF" />
  </AbsoluteFill>;
};

const CompareScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.compare;
  const now = 26 + (g - cuts.compare) / FPS;
  return <AbsoluteFill style={{background: C.bg}}>
    <div style={{position: 'absolute', left: 0, top: 0, width: 960, height: 1080, overflow: 'hidden'}}>
      <FullBleed clip="bunny" start={26} />
    </div>
    <div style={{position: 'absolute', left: 960, top: 0, width: 960, height: 1080, overflow: 'hidden'}}>
      <FullBleed clip="sintel" start={26} />
    </div>
    <div style={{position: 'absolute', left: 959, top: 0, width: 2, height: 1080, background: 'rgba(236,237,239,.32)', boxShadow: '0 0 24px #000'}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 560, background: 'linear-gradient(180deg, rgba(6,7,9,.7), transparent)'}} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 580, bottom: 0, background: 'linear-gradient(180deg, transparent, rgba(6,7,9,.94) 34%)'}} />
    <Caption cue={CUE.compare} offset={cuts.compare} size={132} y={218} maxWidth={1700} />
    <div style={{position: 'absolute', left: 96, top: 340, fontSize: 24, letterSpacing: '.01em', color: C.text, display: 'flex', gap: 14, alignItems: 'center'}}>
      <span style={{width: 34, height: 3, background: C.hot, display: 'inline-block'}} />{LABEL.bunny} <span style={{color: C.hot, fontVariantNumeric: 'tabular-nums'}}>{signed(at(bunny.signals.overall, now))}</span>
    </div>
    <div style={{position: 'absolute', right: 96, top: 340, fontSize: 24, color: C.text, display: 'flex', gap: 14, alignItems: 'center'}}>
      <span style={{width: 34, height: 3, background: C.cool, display: 'inline-block'}} />{LABEL.sintel} <span style={{color: C.cool, fontVariantNumeric: 'tabular-nums'}}>{signed(at(sintel.signals.overall, now))}</span>
    </div>
    <div style={{position: 'absolute', left: 104, right: 104, bottom: 52}}>
      <Graph
        values={bunny.signals.overall}
        comparison={sintel.signals.overall}
        from={14}
        to={42}
        range={bothRange}
        width={1712}
        height={378}
        comparisonColor={C.cool}
        label="compare"
        background
      />
    </div>
    <div style={{position: 'absolute', right: 112, bottom: 22, fontSize: 18, color: C.muted, letterSpacing: '.02em'}}>{LABEL.sameScale}</div>
  </AbsoluteFill>;
};

const ModelScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.model;
  const push = ease(g, cuts.model, cuts.weeksAgain);
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + push * .045 + beat * .003}) translateY(${push * -8}px)`, transformOrigin: '61% 48%'}}>
      <BrainHero start={40} />
    </AbsoluteFill>
    <Shade top="rgba(6,7,9,.2)" bottom="rgba(6,7,9,.92)" />
    <Caption cue={CUE.model} offset={cuts.model} size={92} y={830} maxWidth={1580} />
    <Flash at={cuts.model} offset={cuts.model} strength={.22} length={10} />
  </AbsoluteFill>;
};

/* Placeholder for the 62-65 "Weeks again" beat; stage 2 replaces it with the drained scanner. */
const WeeksScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.weeksAgain;
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{filter: 'grayscale(1) brightness(.6)'}}><Scanner /></AbsoluteFill>
    <Shade top="rgba(6,7,9,.24)" bottom="rgba(6,7,9,.86)" />
    <Caption cue={CUE.weeks} offset={cuts.weeksAgain} size={184} y={540} maxWidth={1760} outFrames={1} loosen={0} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 18, height: 1, background: `rgba(255,138,42,${.16 + beatPulse(g) * .25})`}} />
  </AbsoluteFill>;
};

const EndingScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.minutes;
  const hit = moments.finalHit;
  const build = ease(g, cuts.minutes, hit);
  const slam = ease(g, hit, hit + 12);
  const beat = beatPulse(g);
  const black = ease(g, hit, hit + 7);
  const credits = ease(g, CUE.credits.start + 8, CUE.credits.start + 28);
  return <AbsoluteFill style={{background: '#050607'}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + build * .055 + beat * .002})`, transformOrigin: '54% 48%'}}>
      <BrainHero start={46} />
    </AbsoluteFill>
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 48%, transparent 24%, rgba(5,6,8,.7) 100%)'}} />
    <AbsoluteFill style={{background: '#000', opacity: black}} />
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 44%, rgba(255,138,42,${(.06 + beat * .025) * black}), transparent 54%)`}} />
    <Caption cue={CUE.minutes} offset={cuts.minutes} size={176} y={236} maxWidth={1500} color={C.hot} outFrames={10} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 402, textAlign: 'center', opacity: g >= hit ? 1 : 0, transform: `translateY(-50%) scale(${1.28 - slam * .28 + beat * .007})`, filter: `blur(${(1 - slam) * 11}px)`}}>
      <div style={{fontSize: 238, lineHeight: 1, fontWeight: 600, letterSpacing: '-.07em', color: C.text, textShadow: '0 24px 90px rgba(0,0,0,.8), 0 0 90px rgba(255,138,42,.13)'}}>{CUE.endingTitle.text}</div>
    </div>
    <Caption cue={CUE.endingSub} offset={cuts.minutes} size={57} y={608} maxWidth={1600} weight={400} color={C.muted} letterSpacing={-.025} loosen={3} />
    <div style={{position: 'absolute', left: 250, right: 250, top: 850, textAlign: 'center', fontSize: 23, lineHeight: 1.78, color: C.muted, opacity: credits}}>
      {CREDIT_LINES.map((line) => <div key={line}>{line}</div>)}
    </div>
    <Flash at={hit} offset={cuts.minutes} strength={.5} length={13} />
  </AbsoluteFill>;
};

const BeatOverlay: React.FC = () => {
  const f = useCurrentFrame();
  const pulse = beatPulse(f);
  return <>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 52% 48%, transparent 34%, rgba(0,0,0,${.42 + pulse * .13}) 100%)`, pointerEvents: 'none'}} />
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 48%, rgba(255,138,42,${.016 + pulse * .028}), transparent 62%)`, mixBlendMode: 'screen', pointerEvents: 'none'}} />
  </>;
};

/* Exactly one music bed is audible at a time; the crossfades sit in the music's own stop. */
const musicLevel = (frame: number) => {
  const down = interpolate(frame, [f(44), f(44.15)], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const up = interpolate(frame, [f(48.25), f(48.4)], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  if (frame <= f(44)) return 1;
  if (frame < f(44.15)) return down;
  if (frame < f(48.25)) return 0;
  return up;
};

const impactFrom = f(24 - SFX.impact.HIT_OFFSET);
const impactDuration = f(27) - impactFrom;
const impactFade = f(1.5);
const muteFrom = f(44 - SFX.mute.HIT_OFFSET);
const scribbleFrom = f(64.25 - SFX.scribble.HIT_OFFSET);

export const Trailer: React.FC = () => {
  const frame = useCurrentFrame();
  const pulse = beatPulse(frame);
  return <AbsoluteFill style={{background: C.bg, color: C.text, fontFamily}}>
    <Audio src={staticFile('music3.wav')} volume={(t) => .8 * musicLevel(t)} />
    <Audio src={staticFile('music3-muffled.wav')} volume={(t) => .8 * (1 - musicLevel(t))} />
    <Sequence from={impactFrom} durationInFrames={impactDuration}>
      <Audio src={staticFile(SFX.impact.file)} volume={(t) => interpolate(t, [impactDuration - impactFade, impactDuration], [.6, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})} />
    </Sequence>
    <Sequence from={muteFrom}>
      <Audio src={staticFile(SFX.mute.file)} volume={.5} />
    </Sequence>
    <Sequence from={scribbleFrom}>
      <Audio src={staticFile(SFX.scribble.file)} volume={.5} />
    </Sequence>
    <AbsoluteFill style={{transform: `scale(${1 + pulse * .0035})`, transformOrigin: '50% 50%'}}>
      <Sequence from={cuts.open} durationInFrames={span(cuts.open, cuts.scanner)}><OpenScene /></Sequence>
      <Sequence from={cuts.scanner} durationInFrames={span(cuts.scanner, moments.stop)}><ScannerScene /></Sequence>
      <Sequence from={moments.stop} durationInFrames={span(moments.stop, cuts.drop)}><StopScene /></Sequence>
      <Sequence from={cuts.drop} durationInFrames={span(cuts.drop, cuts.hooked)}><DropScene /></Sequence>
      <Sequence from={cuts.hooked} durationInFrames={span(cuts.hooked, cuts.drift)}><HookedScene /></Sequence>
      <Sequence from={cuts.drift} durationInFrames={span(cuts.drift, cuts.signals)}><DriftScene /></Sequence>
      <Sequence from={cuts.signals} durationInFrames={span(cuts.signals, cuts.mute)}><SignalsScene /></Sequence>
      <Sequence from={cuts.mute} durationInFrames={span(cuts.mute, cuts.compare)}><MutedScene /></Sequence>
      <Sequence from={cuts.compare} durationInFrames={span(cuts.compare, cuts.model)}><CompareScene /></Sequence>
      <Sequence from={cuts.model} durationInFrames={span(cuts.model, cuts.weeksAgain)}><ModelScene /></Sequence>
      <Sequence from={cuts.weeksAgain} durationInFrames={span(cuts.weeksAgain, cuts.minutes)}><WeeksScene /></Sequence>
      <Sequence from={cuts.minutes} durationInFrames={span(cuts.minutes, cuts.end)}><EndingScene /></Sequence>
      <BeatOverlay />
    </AbsoluteFill>
    <AbsoluteFill style={{background: '#000', opacity: ease(frame, DURATION - 16, DURATION - 1), pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
