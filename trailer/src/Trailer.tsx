import React from 'react';
import {
  AbsoluteFill, Audio, OffthreadVideo, Sequence,
  interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import {getInfo} from '@remotion/google-fonts/Geist';
import {loadFontFromInfo} from '@remotion/google-fonts/from-info';
import bunny from './bunny.json';
import sintel from './sintel.json';
import {Scanner} from './scenes/Scanner';
import {BrainHero} from './scenes/BrainHero';
import {CUE, CREDIT_LINES, LABEL, DURATION, FPS, moments, cuts, beatFrames, type Cue} from './script';
import {Graph, at, signed} from './edit/Graph';
import {beatGap, beatPulse, clamp, ease, enter} from './edit/anim';

const info = getInfo();
const localInfo = {...info, fonts: {normal: Object.fromEntries(
  ['400', '500', '600'].map((w) => [w, {latin: staticFile(`geist-${w}.woff2`)}]),
)}, subsets: ['latin'], variable: undefined};
const {fontFamily} = loadFontFromInfo(localInfo, 'normal', {weights: ['400', '500', '600'], subsets: ['latin']});

const C = {
  bg: '#0E0F11', text: '#ECEDEF', muted: '#A0A6AF', rule: '#24262B',
  hot: '#FF8A2A', cool: '#6F7E99', surface: '#17181B',
};

const peak = Math.max(...bunny.signals.overall);
const peakSecond = bunny.signals.overall.indexOf(peak);
const trough = Math.min(...bunny.signals.overall);
const troughSecond = bunny.signals.overall.indexOf(trough);
const both = [...bunny.signals.overall, ...sintel.signals.overall];
const bothLow = Math.min(...both), bothHigh = Math.max(...both);
const bothPad = (bothHigh - bothLow) * .14;
const bothRange: [number, number] = [bothLow - bothPad, bothHigh + bothPad];
const signalArrays = LABEL.signals.map((name) => bunny.signals[name.toLowerCase() as keyof typeof bunny.signals] as number[]);

const span = (a: number, b: number) => Math.max(1, b - a);

const Caption: React.FC<{
  cue: Cue; offset: number; size: number; y?: number; maxWidth?: number;
  color?: string; weight?: number; letterSpacing?: number; loosen?: number; outFrames?: number;
}> = ({cue, offset, size, y = 540, maxWidth = 1560, color = C.text, weight = 600, letterSpacing = -.055, loosen = 2, outFrames = 5}) => {
  const f = useCurrentFrame();
  const g = f + offset;
  const words = cue.text.split(' ');
  const out = 1 - ease(g, cue.end - outFrames, cue.end);
  const beat = beatPulse(g) * clamp((g - cue.start) / 3);
  return <div style={{position: 'absolute', left: 0, right: 0, top: y, transform: 'translateY(-50%)', display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
    <div style={{width: maxWidth, textAlign: 'center', fontSize: size, lineHeight: 1.03, fontWeight: weight, letterSpacing: `${letterSpacing}em`, color, textShadow: '0 8px 42px rgba(0,0,0,.62)', fontVariantNumeric: 'tabular-nums'}}>
      {words.map((word, i) => {
        const delay = cue.start + i * loosen;
        const p = enter(g, delay, 19);
        const shown = clamp((g - delay) / 4);
        const breath = 1 + Math.sin((g - cue.start + i * 3) / 21) * .006;
        return <React.Fragment key={`${word}-${i}`}>
          {i > 0 ? ' ' : ''}
          <span style={{display: 'inline-block', opacity: shown * out, transform: `translateY(${(1 - p) * 42}px) scale(${(.94 + p * .06) * breath + beat * .012})`, filter: `blur(${(1 - clamp(p)) * 11}px)`}}>{word}</span>
        </React.Fragment>;
      })}
    </div>
  </div>;
};

const Flash: React.FC<{at: number; offset?: number; length?: number; strength?: number; color?: string}> = ({
  at: hit, offset = 0, length = 8, strength = .38, color = '#FFFFFF',
}) => {
  const g = useCurrentFrame() + offset;
  const amount = Math.max(0, 1 - (g - hit) / length) * (g >= hit - 1 ? 1 : 0);
  return <AbsoluteFill style={{background: color, opacity: amount * amount * strength, pointerEvents: 'none', mixBlendMode: 'screen'}} />;
};

const Shade: React.FC<{top?: string; bottom?: string}> = ({top = 'transparent', bottom = 'rgba(7,8,10,.82)'}) =>
  <AbsoluteFill style={{background: `linear-gradient(180deg, ${top} 0%, transparent 32%, transparent 54%, ${bottom} 100%)`, pointerEvents: 'none'}} />;

const FullBleed: React.FC<{clip: 'bunny' | 'sintel'; start: number; blur?: number}> = ({clip, start, blur = 0}) => {
  const f = useCurrentFrame();
  return <AbsoluteFill style={{overflow: 'hidden', background: C.bg}}>
    <OffthreadVideo
      src={staticFile(clip === 'bunny' ? 'bbb_720p_55s.mp4' : 'sintel_trailer_720p.mp4')}
      trimBefore={Math.round(start * FPS)}
      muted
      style={{width: '100%', height: '100%', objectFit: 'cover', filter: `blur(${blur}px) saturate(1.03)`}} />
    <AbsoluteFill style={{background: 'linear-gradient(110deg, rgba(6,7,9,.56), transparent 46%, rgba(6,7,9,.2))'}} />
  </AbsoluteFill>;
};

/* Opening: one continuous Scanner push-in, captions riding the build. */
const OpenScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.open;
  const push = clamp((g - cuts.open) / span(cuts.open, cuts.stop));
  const beat = beatPulse(g);
  const buildFlash = Math.max(...CUE.build.map((cue) => clamp(1 - (g - cue.start) / 7)));
  return <AbsoluteFill style={{background: C.bg}}>
    <Sequence durationInFrames={span(cuts.open, CUE.openB.start)}>
      <AbsoluteFill style={{transform: `scale(${1.04 + push * .1})`}}><FullBleed clip="bunny" start={20} /></AbsoluteFill>
    </Sequence>
    <Sequence from={CUE.openB.start - cuts.open} durationInFrames={span(CUE.openB.start, cuts.build)}>
      <AbsoluteFill style={{transform: `scale(${1.04 + push * .1})`}}><FullBleed clip="sintel" start={14} /></AbsoluteFill>
    </Sequence>
    <Sequence from={cuts.build - cuts.open} durationInFrames={span(cuts.build, cuts.stop)}>
      <AbsoluteFill style={{transform: `scale(${1.025 + push * .17 + beat * .004}) translateY(${push * -12}px)`, transformOrigin: '52% 52%'}}>
        <Scanner />
      </AbsoluteFill>
    </Sequence>
    <AbsoluteFill style={{background: '#000', opacity: ease(g, CUE.stopA.start, CUE.stopA.start + 8) * .62, pointerEvents: 'none'}} />
    <Shade top="rgba(7,8,10,.24)" bottom="rgba(7,8,10,.78)" />
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 53%, transparent 28%, rgba(5,6,8,.42) 100%)'}} />
    <Caption cue={CUE.openA} offset={cuts.open} size={103} y={505} maxWidth={1450} />
    <Caption cue={CUE.openB} offset={cuts.open} size={103} y={505} maxWidth={1450} />
    <Caption cue={CUE.answer} offset={cuts.open} size={96} y={505} maxWidth={1500} />
    {CUE.build.map((cue) => <Caption key={cue.id} cue={cue} offset={cuts.open} size={184} y={540} maxWidth={1760} outFrames={1} />)}
    <Caption cue={CUE.stopA} offset={cuts.open} size={132} y={480} maxWidth={1520} outFrames={1} loosen={0} />
    <AbsoluteFill style={{background: '#FFFFFF', opacity: buildFlash * .12, mixBlendMode: 'screen', pointerEvents: 'none'}} />
    <Flash at={cuts.build} offset={cuts.open} strength={.18} length={10} />
  </AbsoluteFill>;
};

const StopScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.stop;
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: '#000'}}>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 52%, rgba(255,138,42,${.012 + beat * .012}), transparent 55%)`}} />
    <Caption cue={CUE.stopA} offset={cuts.stop} size={132} y={480} maxWidth={1520} outFrames={1} loosen={0} />
    <Caption cue={CUE.stopB} offset={cuts.stop} size={148} y={620} maxWidth={1520} outFrames={1} loosen={0} />
  </AbsoluteFill>;
};

const DropScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.drop;
  const slam = ease(g, cuts.drop, cuts.drop + 11);
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: '#050607'}}>
    <AbsoluteFill style={{transform: `scale(${1.16 - slam * .16 + beat * .003})`, filter: `blur(${(1 - slam) * 9}px)`}}>
      <BrainHero start={12} />
    </AbsoluteFill>
    <Shade top="rgba(7,8,10,.18)" bottom="rgba(7,8,10,.9)" />
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 66% 44%, rgba(255,138,42,.09), transparent 50%)'}} />
    <Caption cue={CUE.stopB} offset={cuts.drop} size={148} y={620} maxWidth={1520} outFrames={6} loosen={0} />
    <Caption cue={CUE.drop} offset={cuts.drop} size={128} y={856} maxWidth={1700} />
    <Flash at={cuts.drop} offset={cuts.drop} strength={.56} length={12} />
  </AbsoluteFill>;
};

/* Full-bleed montage, cutting with the downbeat. */
const MontageShot: React.FC<{from: number; to: number; sources: [number, number]; clips: ['bunny' | 'sintel', 'bunny' | 'sintel']}> = ({from, to, sources, clips}) => {
  const f = useCurrentFrame();
  const g = f + from;
  const mid = from + (to - from) * .5;
  const switchAt = beatFrames.find((beat) => beat >= mid && beat < to) ?? Math.round(mid);
  const first = g < switchAt;
  const whip = Math.max(0, 1 - Math.abs(g - switchAt) / 4.5);
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + whip * .025}) translateX(${first ? -whip * 10 : whip * 10}px)`}}>
      <FullBleed clip={first ? clips[0] : clips[1]} start={first ? sources[0] : sources[1]} blur={whip * 6} />
    </AbsoluteFill>
    <Flash at={switchAt} offset={from} strength={.2} length={6} />
  </AbsoluteFill>;
};

const HookedScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.hooked;
  const grow = ease(g, cuts.hooked + 3, cuts.drift - 7);
  return <AbsoluteFill style={{background: C.bg}}>
    <MontageShot from={cuts.hooked} to={cuts.drift} sources={[50, 13]} clips={['bunny', 'sintel']} />
    <Shade top="rgba(6,7,9,.5)" bottom="rgba(6,7,9,.94)" />
    <Caption cue={CUE.hooked} offset={cuts.hooked} size={121} y={330} maxWidth={1720} />
    <div style={{position: 'absolute', left: 96, right: 96, bottom: 58, opacity: .96}}>
      <Graph
        values={bunny.signals.overall}
        from={38}
        to={54.8}
        range={[-.15, .25]}
        width={1728}
        height={352}
        progress={.12 + grow * .88}
        marker={{second: peakSecond, label: `${signed(peak)} \u00b7 ${peakSecond}${LABEL.seconds}`}}
        label="hooked"
        background
      />
    </div>
    <Flash at={cuts.hooked} offset={cuts.hooked} strength={.26} length={9} />
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 18, height: 1, background: `rgba(255,138,42,${.16 + beatPulse(g) * .25})`}} />
  </AbsoluteFill>;
};

const DriftScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.drift;
  const reveal = ease(g, cuts.drift + 2, cuts.signals - 2);
  return <AbsoluteFill style={{background: C.bg}}>
    <MontageShot from={cuts.drift} to={cuts.signals} sources={[30, 5]} clips={['bunny', 'sintel']} />
    <Shade top="rgba(6,7,9,.72)" bottom="rgba(6,7,9,.88)" />
    <Caption cue={CUE.drift} offset={cuts.drift} size={126} y={746} maxWidth={1740} />
    <div style={{position: 'absolute', left: 150, right: 150, top: 92, opacity: .95}}>
      <Graph
        values={bunny.signals.overall}
        from={20}
        to={42}
        range={[-.15, .08]}
        width={1620}
        height={370}
        progress={reveal}
        marker={{second: troughSecond, label: `${signed(trough, 3)} \u00b7 ${troughSecond}${LABEL.seconds}`}}
        label="drift"
        background
      />
    </div>
    <Flash at={cuts.drift} offset={cuts.drift} strength={.2} length={8} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 18, height: 1, background: `rgba(111,126,153,${.22 + beatPulse(g) * .2})`}} />
  </AbsoluteFill>;
};

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
  return <div style={{position: 'absolute', left: x - 230, top: y - 46 + drift, width: 460, textAlign: 'center', opacity: clamp((g - cue.start) / 4) * (1 - ease(g, cuts.muted - 4, cuts.muted)), transform: `scale(${.62 + p * .38 + beat * .05})`, filter: `blur(${(1 - clamp(p)) * 8}px)`, pointerEvents: 'none'}}>
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
    <Caption cue={CUE.signals} offset={cuts.signals} size={126} y={540} maxWidth={1340} />
    {CUE.signalNames.map((cue, i) => <SignalWord key={cue.id} cue={cue} index={i} offset={cuts.signals} />)}
    <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 3, background: `linear-gradient(90deg, transparent, rgba(255,138,42,${.2 + beatPulse(g) * .4}), transparent)`}} />
  </AbsoluteFill>;
};

const MutedScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.muted;
  const now = 25 + (g - cuts.muted) / FPS;
  const split = ease(g, moments.duck, moments.duck + 18);
  const duck = Math.max(0, 1 - Math.abs(g - moments.duck) / 4);
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + duck * .012})`}}>
      <FullBleed clip="bunny" start={25} blur={duck * 1.2} />
    </AbsoluteFill>
    <Shade top="rgba(6,7,9,.62)" bottom="rgba(6,7,9,.96)" />
    <Caption cue={CUE.muted} offset={cuts.muted} size={126} y={244} maxWidth={1740} />
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
    <Flash at={moments.duck} offset={cuts.muted} strength={.18} length={7} color="#DCE8FF" />
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
  const push = ease(g, cuts.model, cuts.ending);
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + push * .045 + beat * .003}) translateY(${push * -8}px)`, transformOrigin: '61% 48%'}}>
      <BrainHero start={40} />
    </AbsoluteFill>
    <Shade top="rgba(6,7,9,.2)" bottom="rgba(6,7,9,.92)" />
    <Flash at={cuts.model} offset={cuts.model} strength={.22} length={10} />
  </AbsoluteFill>;
};

const EndingScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.ending;
  const hit = moments.endingHit;
  const build = ease(g, cuts.ending, hit);
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
    <div style={{position: 'absolute', left: 0, right: 0, top: 402, textAlign: 'center', opacity: g >= hit ? 1 : 0, transform: `translateY(-50%) scale(${1.28 - slam * .28 + beat * .007})`, filter: `blur(${(1 - slam) * 11}px)`}}>
      <div style={{fontSize: 238, lineHeight: 1, fontWeight: 600, letterSpacing: '-.07em', color: C.text, textShadow: '0 24px 90px rgba(0,0,0,.8), 0 0 90px rgba(255,138,42,.13)'}}>{CUE.endingTitle.text}</div>
    </div>
    <Caption cue={CUE.endingSub} offset={cuts.ending} size={57} y={608} maxWidth={1600} weight={400} color={C.muted} letterSpacing={-.025} loosen={3} />
    <div style={{position: 'absolute', left: 250, right: 250, top: 850, textAlign: 'center', fontSize: 23, lineHeight: 1.78, color: C.muted, opacity: credits}}>
      {CREDIT_LINES.map((line) => <div key={line}>{line}</div>)}
    </div>
    <Flash at={hit} offset={cuts.ending} strength={.5} length={13} />
  </AbsoluteFill>;
};

/* Spans the model and ending scenes: the model line, then "Weeks" struck through and "minutes." on the next bar. */
const LateCaptions: React.FC = () => {
  const g = useCurrentFrame();
  const strike = ease(g, CUE.minutes.start - 10, CUE.minutes.start);
  const shown = g >= CUE.weeks.start && g < CUE.weeks.end;
  const out = 1 - ease(g, CUE.weeks.end - 5, CUE.weeks.end);
  return <>
    <AbsoluteFill style={{background: '#000', opacity: (ease(g, CUE.weeks.start - 6, CUE.weeks.start + 4) - ease(g, moments.endingHit, moments.endingHit + 1)) * .72, pointerEvents: 'none'}} />
    <Caption cue={CUE.model} offset={0} size={92} y={830} maxWidth={1580} />
    {shown && <div style={{position: 'absolute', left: 0, right: 0, top: 540, transform: 'translateY(-50%)', display: 'flex', justifyContent: 'center', alignItems: 'baseline', gap: 56, opacity: out, pointerEvents: 'none'}}>
      <div style={{position: 'relative', fontSize: 184, fontWeight: 600, letterSpacing: '-.055em', color: strike > 0 ? C.muted : C.text, opacity: 1 - strike * .45, textShadow: '0 8px 42px rgba(0,0,0,.62)', transform: `scale(${.92 + enter(g, CUE.weeks.start, 19) * .08})`}}>
        {CUE.weeks.text}
        <div style={{position: 'absolute', left: -12, right: -12, top: '54%', height: 12, borderRadius: 6, background: C.hot, transform: `scaleX(${strike})`, transformOrigin: 'left'}} />
      </div>
      {g >= CUE.minutes.start && <div style={{fontSize: 184, fontWeight: 600, letterSpacing: '-.055em', color: C.hot, textShadow: '0 0 70px rgba(255,138,42,.35)', transform: `scale(${1.25 - enter(g, CUE.minutes.start, 14) * .25})`, filter: `blur(${(1 - clamp(enter(g, CUE.minutes.start, 14))) * 10}px)`}}>{CUE.minutes.text}</div>}
    </div>}
    <Flash at={CUE.minutes.start} strength={.3} length={10} />
  </>;
};

const BeatOverlay: React.FC = () => {
  const f = useCurrentFrame();
  const pulse = beatPulse(f);
  return <>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 52% 48%, transparent 34%, rgba(0,0,0,${.42 + pulse * .13}) 100%)`, pointerEvents: 'none'}} />
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 48%, rgba(255,138,42,${.016 + pulse * .028}), transparent 62%)`, mixBlendMode: 'screen', pointerEvents: 'none'}} />
  </>;
};

const duckVolume = (frame: number) => {
  const beat = beatGap(moments.duck);
  const d = moments.duck;
  if (frame <= d - 2) return 1;
  if (frame < d + beat) return interpolate(frame, [d - 2, d, d + beat - 1], [1, .14, .14], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  if (frame < d + beat + 8) return interpolate(frame, [d + beat - 1, d + beat + 8], [.14, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return 1;
};

export const Trailer: React.FC = () => {
  const f = useCurrentFrame();
  const pulse = beatPulse(f);
  return <AbsoluteFill style={{background: C.bg, color: C.text, fontFamily}}>
    <Audio src={staticFile('music-edit.wav')} volume={duckVolume} />
    <AbsoluteFill style={{transform: `scale(${1 + pulse * .0035})`, transformOrigin: '50% 50%'}}>
      <Sequence from={cuts.open} durationInFrames={span(cuts.open, cuts.stop)}><OpenScene /></Sequence>
      <Sequence from={cuts.stop} durationInFrames={span(cuts.stop, cuts.drop)}><StopScene /></Sequence>
      <Sequence from={cuts.drop} durationInFrames={span(cuts.drop, cuts.hooked)}><DropScene /></Sequence>
      <Sequence from={cuts.hooked} durationInFrames={span(cuts.hooked, cuts.drift)}><HookedScene /></Sequence>
      <Sequence from={cuts.drift} durationInFrames={span(cuts.drift, cuts.signals)}><DriftScene /></Sequence>
      <Sequence from={cuts.signals} durationInFrames={span(cuts.signals, cuts.muted)}><SignalsScene /></Sequence>
      <Sequence from={cuts.muted} durationInFrames={span(cuts.muted, cuts.compare)}><MutedScene /></Sequence>
      <Sequence from={cuts.compare} durationInFrames={span(cuts.compare, cuts.model)}><CompareScene /></Sequence>
      <Sequence from={cuts.model} durationInFrames={span(cuts.model, cuts.ending)}><ModelScene /></Sequence>
      <Sequence from={cuts.ending} durationInFrames={span(cuts.ending, cuts.end)}><EndingScene /></Sequence>
      <LateCaptions />
      <BeatOverlay />
    </AbsoluteFill>
    <AbsoluteFill style={{background: '#000', opacity: ease(f, DURATION - 16, DURATION - 1), pointerEvents: 'none'}} />
  </AbsoluteFill>;
};
