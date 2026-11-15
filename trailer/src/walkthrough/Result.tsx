import React from 'react';
import {
  AbsoluteFill, Easing, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import swee from '../../public/walkthrough/swee/result.json';
import {Graph} from '../edit/Graph';
import {C, fontFamily} from '../edit/ui';
import {ScreenWindow, SourceMark} from './MacWindow';
import {FPS, RESULT, SWEE, at, sec} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const easeIO = (t: number, a: number, b: number) => interpolate(t, [a, b], [0, 1], {
  extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  easing: Easing.inOut(Easing.cubic),
});
const easeOut = (t: number, a: number, b: number) => interpolate(t, [a, b], [0, 1], {
  extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  easing: Easing.out(Easing.cubic),
});

const overall = swee.signals.overall as number[];
const muted = swee.muted.overall as number[];
const shared = Math.min(overall.length, muted.length);
const all = [...overall.slice(0, shared), ...muted.slice(0, shared)];
const low = Math.min(...all);
const high = Math.max(...all);
const pad = (high - low) * 0.12 || 0.04;
const RANGE: [number, number] = [low - pad, high + pad];

const Soft: React.FC<{total: number; children: React.ReactNode}> = ({total, children}) => {
  const f = useCurrentFrame();
  const opacity = interpolate(f, [0, 6, total - 6, total], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  return <AbsoluteFill style={{opacity}}>{children}</AbsoluteFill>;
};

const Beat: React.FC<{from: number; to: number; children: React.ReactNode}> = ({from, to, children}) => {
  const total = sec(to - from);
  return <Sequence from={sec(from - RESULT.start)} durationInFrames={total}>
    <Soft total={total}>{children}</Soft>
  </Sequence>;
};

const Label: React.FC<{text: string; x?: number; y?: number}> = ({text, x = 120, y = 56}) =>
  <div style={{
    position: 'absolute', left: x, top: y, fontFamily, fontSize: 18, fontWeight: 500,
    letterSpacing: '.14em', textTransform: 'uppercase', color: C.muted,
  }}>{text}</div>;

const CameBack: React.FC = () => {
  return <AbsoluteFill>
    <Label text="The result" />
    <ScreenWindow
      title="NeuroLens — Analysis"
      x={110} y={100} w={1700} h={896}
      src="walkthrough/s5_result.mp4"
      kind="video"
      sourceW={2940} sourceH={1912}
      sourceStart={0.2}
      rate={0.85}
      base={{x: 0, y: 400, w: 2940, h: 1490}}
      cams={[{at: 0, cx: 1470, cy: 1145, zoom: 1}]}
    />
  </AbsoluteFill>;
};

const ChartMoment: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  return <AbsoluteFill>
    <Label text="Every second" />
    <ScreenWindow
      title="NeuroLens — Analysis"
      x={140} y={130} w={1640} h={790}
      src="walkthrough/s6.mp4"
      kind="video"
      sourceW={2940} sourceH={1912}
      sourceStart={1.0}
      base={{x: 880, y: 390, w: 2040, h: 940}}
      cams={[
        {at: 0, cx: 1900, cy: 860, zoom: 1},
      ]}
    >
      <SourceMark x={918} y={870} w={1880} h={46} opacity={t > 2.2 && t < 6.4 ? 1 : 0} radius={12} />
    </ScreenWindow>
  </AbsoluteFill>;
};

const Report: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const inX = 140 + (1 - easeOut(t, 0, 0.65)) * 70;
  return <AbsoluteFill>
    <Label text="The report" />
    <ScreenWindow
      title="NeuroLens — Report"
      x={inX} y={120} w={1640} h={802}
      src="walkthrough/s9.mp4"
      kind="video"
      sourceW={2940} sourceH={1912}
      sourceStart={1.0}
      base={{x: 880, y: 390, w: 2040, h: 956}}
    />
  </AbsoluteFill>;
};

const Photos: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const rise = 1 - easeOut(t, 0, 0.7);
  return <AbsoluteFill>
    <Label text="The old photos · 0:08–0:13" />
    <ScreenWindow
      title="NeuroLens — Report"
      x={70} y={365 + rise * 14} w={1320} h={350}
      src="walkthrough/captures/report_moment_1.png"
      kind="image"
      sourceW={2313} sourceH={480}
      base={{x: 0, y: 0, w: 2313, h: 480}}
      fit="contain"
      contentBg="#0C0D10"
    />
    <div style={{
      position: 'absolute', left: 1430, top: 166, width: 420, height: 748,
      borderRadius: 18, overflow: 'hidden', background: '#000',
      border: `1px solid rgba(255,255,255,.10)`,
      boxShadow: '0 38px 100px rgba(0,0,0,.8), 0 0 0 1px rgba(255,255,255,.04)',
    }}>
      <OffthreadVideo src={staticFile(SWEE.video)} trimBefore={sec(8)} muted
        style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      <div style={{
        position: 'absolute', left: 16, bottom: 16, padding: '6px 12px', borderRadius: 8,
        background: 'rgba(0,0,0,.68)', fontFamily, fontSize: 20, fontWeight: 600,
        color: C.text, fontVariantNumeric: 'tabular-nums',
      }}>0:08 – 0:13</div>
    </div>
  </AbsoluteFill>;
};

const MuteFall: React.FC<{t: number}> = ({t}) => {
  const from = 8;
  const to = 13;
  const withSound = overall.slice(from, to + 1);
  const withoutSound = muted.slice(from, to + 1);
  const values = [...withSound, ...withoutSound];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const p = (hi - lo) * 0.18 || 0.04;
  const range: [number, number] = [lo - p, hi + p];
  const morph = easeIO(t, 0.7, 2.0);
  const compare = withSound.map((v, i) => v + (withoutSound[i] - v) * morph);
  const tick = easeOut(t, 0.35, 0.65);
  const W = 1320, H = 430;
  const zeroY = 12 + ((range[1] - 0) / (range[1] - range[0])) * (H - 24);
  return <div style={{
    position: 'absolute', left: 260, top: 150, width: 1400, height: 760,
    borderRadius: 16, background: C.surface, border: `1px solid ${C.rule}`,
    boxShadow: '0 34px 100px rgba(0,0,0,.6)', overflow: 'hidden', fontFamily,
  }}>
    <div style={{position: 'absolute', left: 40, top: 32, fontSize: 22, color: C.muted, letterSpacing: '-0.005em'}}>
      0:08 – 0:13
    </div>
    <div style={{position: 'absolute', right: 40, top: 26, display: 'flex', alignItems: 'center', gap: 13}}>
      <div style={{
        width: 32, height: 32, borderRadius: 8, boxSizing: 'border-box',
        border: `2px solid ${tick > 0 ? C.hot : 'rgba(160,166,175,.5)'}`,
        background: `rgba(255,138,42,${tick})`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <svg width={20} height={20} viewBox="0 0 20 20" style={{opacity: tick}}>
          <path d="M4 10.5 L8.5 15 L16 5.5" fill="none" stroke="#000" strokeWidth={2.6}
            strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <span style={{fontSize: 27, color: C.text, letterSpacing: '-0.02em'}}>Without sound</span>
    </div>
    <div style={{position: 'absolute', left: 40, right: 40, top: 98, height: H}}>
      <div style={{position: 'absolute', left: 10, top: zeroY, width: W - 20, borderTop: '1px dashed rgba(160,166,175,.45)'}} />
      <Graph
        values={withSound}
        comparison={compare}
        from={0}
        to={to - from}
        range={range}
        width={W}
        height={H}
        progress={1}
        comparisonProgress={1}
        color={C.hot}
        comparisonColor="rgba(236,237,239,.72)"
        comparisonDashed
        axes={false}
        label="mute-fall"
        strokeWidth={4}
      />
      <div style={{
        position: 'absolute', left: 10, top: zeroY - 28, fontSize: 19, color: C.muted,
      }}>typical viewing</div>
    </div>
    <div style={{
      position: 'absolute', left: 40, bottom: 34, display: 'flex', gap: 36,
      fontSize: 20, color: C.text,
    }}>
      <span style={{display: 'flex', alignItems: 'center', gap: 10}}>
        <span style={{width: 30, height: 3, background: C.hot, borderRadius: 2}} /> with sound
      </span>
      <span style={{display: 'flex', alignItems: 'center', gap: 10}}>
        <span style={{width: 30, borderTop: '3px dashed rgba(236,237,239,.72)'}} /> without sound
      </span>
    </div>
  </div>;
};

const Mute: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const a = t < 2.4;
  const b = t >= 2.1 && t < 5.5;
  const c = t >= 5.2;
  const aOut = 1 - easeIO(t, 2.1, 2.4);
  const bIn = easeIO(t, 2.1, 2.4);
  const bOut = 1 - easeIO(t, 5.2, 5.5);
  const cIn = easeIO(t, 5.2, 5.5);
  return <AbsoluteFill>
    <Label text="Why the moment drops" />
    {a && <AbsoluteFill style={{opacity: aOut}}>
      <ScreenWindow
        title="NeuroLens — Analysis"
        x={140} y={130} w={1640} h={790}
        src="walkthrough/s10.mp4"
        kind="video"
        sourceW={2940} sourceH={1912}
        sourceStart={2.0}
        base={{x: 880, y: 390, w: 2040, h: 940}}
      >
        <SourceMark x={1328} y={588} w={245} h={42} opacity={t > 0.4 ? 1 : 0} radius={9} />
      </ScreenWindow>
    </AbsoluteFill>}
    {b && <AbsoluteFill style={{opacity: bIn * bOut}}><MuteFall t={t - 2.1} /></AbsoluteFill>}
    {c && <AbsoluteFill style={{opacity: cIn}}>
      <ScreenWindow
        title="NeuroLens — Report"
        x={180} y={350} w={1560} h={390}
        src="walkthrough/captures/report_moment_1.png"
        kind="image"
        sourceW={2313} sourceH={480}
        base={{x: 0, y: 0, w: 2313, h: 480}}
        fit="contain"
        contentBg="#0C0D10"
      >
        <SourceMark x={0} y={320} w={2310} h={120} opacity={t > 6.2 ? 1 : 0} radius={8} />
      </ScreenWindow>
    </AbsoluteFill>}
  </AbsoluteFill>;
};

const Alley: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const W = 980, H = 500;
  const px = 10 + ((29 - 0) / (shared - 1)) * (W - 20);
  const value = overall[29];
  const py = 12 + ((RANGE[1] - value) / (RANGE[1] - RANGE[0])) * (H - 24);
  const ring = easeOut(t, 1.2, 1.7);
  return <AbsoluteFill>
    <Label text="The back-alley reveal · 0:29" />
    <div style={{
      position: 'absolute', left: 120, top: 160, width: 1040, height: 650,
      borderRadius: 16, background: C.surface, border: `1px solid ${C.rule}`,
      boxShadow: '0 34px 100px rgba(0,0,0,.6)', fontFamily,
    }}>
      <div style={{position: 'absolute', left: 34, top: 28, fontSize: 24, color: C.text, fontWeight: 600, letterSpacing: '-0.02em'}}>
        Overall response
      </div>
      <div style={{position: 'absolute', left: 30, top: 100, width: W, height: H}}>
        <Graph
          values={overall}
          comparison={muted}
          from={0}
          to={shared - 1}
          range={RANGE}
          width={W}
          height={H}
          progress={1}
          comparisonProgress={1}
          color={C.hot}
          comparisonColor="rgba(236,237,239,.72)"
          comparisonDashed
          axes={false}
          label="alley-peak"
          marker={{second: 29, label: '0:29', size: 22}}
          strokeWidth={4}
        />
        <div style={{
          position: 'absolute', left: px - 34, top: py - 34, width: 68, height: 68,
          borderRadius: 999, border: `2px solid ${C.hot}`, opacity: ring,
          boxShadow: '0 0 26px rgba(255,138,42,.45)',
        }} />
      </div>
    </div>
    <div style={{
      position: 'absolute', left: 1240, top: 150, width: 340, height: 604,
      borderRadius: 18, overflow: 'hidden', background: '#000',
      border: `1px solid ${C.hot}`, boxShadow: '0 38px 100px rgba(0,0,0,.8), 0 0 40px rgba(255,138,42,.16)',
    }}>
      <OffthreadVideo src={staticFile(SWEE.video)} trimBefore={sec(29)} muted
        style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      <div style={{
        position: 'absolute', left: 14, bottom: 14, padding: '5px 10px', borderRadius: 7,
        background: 'rgba(0,0,0,.62)', fontFamily, fontSize: 17, fontWeight: 600, color: C.text,
      }}>0:29</div>
    </div>
  </AbsoluteFill>;
};

const Compare: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  return <AbsoluteFill>
    <Label text="CeraVe ad · two cuts" />
    <ScreenWindow
      title="NeuroLens — Compare"
      x={130} y={102} w={1660} h={900}
      src="walkthrough/s11.mp4"
      kind="video"
      sourceW={2940} sourceH={1912}
      sourceStart={0.5}
      base={{x: 20, y: 390, w: 2900, h: 1515}}
      cams={[
        {at: 0, cx: 1470, cy: 1148, zoom: 1},
      ]}
    >
      <SourceMark x={2548} y={516} w={178} h={44} opacity={t > 0.8 && t < 2.3 ? 1 : 0} radius={9} />
    </ScreenWindow>
  </AbsoluteFill>;
};

export const Result: React.FC = () => {
  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 45%, #0A0B0D 0%, #000 72%)', fontFamily, color: C.text}}>
    <Beat from={RESULT.start} to={at(15)}><CameBack /></Beat>
    <Beat from={at(15)} to={at(16)}><ChartMoment /></Beat>
    <Beat from={at(16)} to={at(17)}><Report /></Beat>
    <Beat from={at(17)} to={at(18)}><Photos /></Beat>
    <Beat from={at(18)} to={at(19)}><Mute /></Beat>
    <Beat from={at(19)} to={at(20)}><Alley /></Beat>
    <Beat from={at(20)} to={RESULT.end}><Compare /></Beat>
  </AbsoluteFill>;
};
