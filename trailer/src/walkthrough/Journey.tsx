import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {
  AbsoluteFill, Easing, Img, Sequence, interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import swee from '../../public/walkthrough/swee/result.json';
import {Graph} from '../edit/Graph';
import {C, fontFamily} from '../edit/ui';
import {Cortex} from '../scenes/brain/Cortex';
import {useSweeBrain} from './brainData';
import {ScreenWindow, SourceMark} from './MacWindow';
import {FPS, JOURNEY, MAP, MAP_H, MAP_W, SCENES, at, sec} from './timeline';

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
const withSound = overall.slice(0, shared);
const withoutSound = muted.slice(0, shared);
const all = [...withSound, ...withoutSound];
const low = Math.min(...all);
const high = Math.max(...all);
const pad = (high - low) * 0.12 || 0.04;
const RANGE: [number, number] = [low - pad, high + pad];
const GRAPH = {x: 22, y: 84, w: 576, h: 306};

const Card: React.FC<{
  x: number; y: number; w: number; h: number;
  title: string; label: string;
  appear: number; t: number;
  glow?: number; dim?: number;
  children?: React.ReactNode;
}> = ({x, y, w, h, title, label, appear, t, glow = 0, dim = 0, children}) => {
  const p = easeOut(t, appear, appear + 0.45);
  return <div style={{
    position: 'absolute', left: x, top: y, width: w, height: h,
    opacity: p * (1 - dim * 0.6), transform: `translateY(${(1 - p) * 12}px)`,
    background: C.surface, borderRadius: 14, boxSizing: 'border-box',
    border: `1px solid ${glow > 0.02 ? C.hot : C.rule}`,
    boxShadow: glow > 0.02
      ? `0 0 0 1px ${C.hot}, 0 0 40px rgba(255,138,42,${0.16 + glow * 0.24})`
      : '0 14px 36px rgba(0,0,0,.38)',
    display: 'flex', flexDirection: 'column', justifyContent: 'center',
    padding: '0 22px', fontFamily, color: C.text,
  }}>
    <div style={{
      fontSize: w > 400 ? 40 : 34, lineHeight: 1.1, fontWeight: 600,
      letterSpacing: '-0.03em',
    }}>{title}</div>
    <div style={{marginTop: 9, fontSize: 19, lineHeight: 1.3, color: C.muted, letterSpacing: '-0.005em'}}>
      {label}
    </div>
    {children}
  </div>;
};

const camKeys = [
  {at: JOURNEY.start, cx: 820, cy: 470},
  {at: at(5) - 0.2, cx: 900, cy: 520},
  {at: at(6) - 0.2, cx: 1320, cy: 470},
  {at: at(7) - 0.2, cx: 2180, cy: 460},
  {at: at(9) - 0.35, cx: 2380, cy: 600},
  {at: at(10) - 0.25, cx: 2870, cy: 600},
  {at: at(11) - 0.3, cx: 1770, cy: 520},
  {at: at(12) - 0.3, cx: 2890, cy: 470},
  {at: at(13) - 0.25, cx: 2180, cy: 460},
];
const MOVE = 1.15;

const cameraAt = (t: number) => {
  let index = 0;
  for (let i = 0; i < camKeys.length; i++) if (t >= camKeys[i].at) index = i;
  const key = camKeys[index];
  const prev = camKeys[Math.max(0, index - 1)];
  const p = index === 0 ? 0 : easeIO(t, key.at, key.at + MOVE);
  return {cx: prev.cx + (key.cx - prev.cx) * p, cy: prev.cy + (key.cy - prev.cy) * p};
};

const dotKeys = [
  {at: JOURNEY.browser + 0.2, x: 250, y: 385},
  {at: JOURNEY.browser + 1.5, x: 710, y: 385},
  {at: JOURNEY.queue - 0.1, x: 710, y: 385},
  {at: JOURNEY.queue + 1.1, x: 1230, y: 385},
  {at: JOURNEY.check + 0.2, x: 1710, y: 385},
  {at: JOURNEY.gpuWake, x: 2230, y: 385},
  {at: JOURNEY.brain + 0.1, x: 2230, y: 790},
  {at: JOURNEY.brain + 1.2, x: 2370, y: 790},
  {at: JOURNEY.secondLine, x: 3070, y: 790},
  {at: at(11) + 1, x: 1710, y: 645},
  {at: at(12) + 1, x: 3070, y: 395},
  {at: JOURNEY.end, x: 3070, y: 395},
];

const dotAt = (t: number) => {
  if (t < dotKeys[0].at) return null;
  for (let i = 1; i < dotKeys.length; i++) {
    const a = dotKeys[i - 1];
    const b = dotKeys[i];
    if (t <= b.at) {
      const p = easeIO(t, Math.max(a.at, b.at - 1.6), b.at);
      return {x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p};
    }
  }
  const last = dotKeys[dotKeys.length - 1];
  return {x: last.x, y: last.y};
};

const Path: React.FC<{d: string; opacity: number; dashed?: boolean; color?: string}> = ({d, opacity, dashed, color = '#4B515A'}) =>
  <path d={d} fill="none" stroke={color} strokeWidth={2} markerEnd="url(#map-arrow)"
    strokeDasharray={dashed ? '8 9' : undefined} opacity={opacity} />;

const dots = [
  'walkthrough/swee/frames/f5.jpg',
  'walkthrough/swee/frames/f15.jpg',
  'walkthrough/swee/frames/f25.jpg',
];

const BrainPanel: React.FC<{t: number; appear: number}> = ({t, appear}) => {
  const data = useSweeBrain();
  const p = easeOut(t, appear, appear + 0.5);
  const turn = easeIO(t, at(9), at(10) + 2);
  return <div style={{
    position: 'absolute', left: MAP.brain.x, top: MAP.brain.y, width: MAP.brain.w, height: MAP.brain.h,
    borderRadius: 14, background: '#0F1013', border: `1px solid ${C.rule}`,
    opacity: p, overflow: 'hidden', boxShadow: '0 18px 50px rgba(0,0,0,.45)',
  }}>
    {data && <ThreeCanvas
      width={MAP.brain.w} height={MAP.brain.h}
      style={{position: 'absolute', left: 0, top: 0}}
      camera={{fov: 28, near: 0.1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
      gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
      dpr={1}
    >
      <Cortex data={data} second={Math.round(8 + turn * 6)} progress={0.18 + turn * 0.42} />
    </ThreeCanvas>}
    <div style={{
      position: 'absolute', left: 18, top: 16, fontFamily, fontSize: 17,
      fontWeight: 500, letterSpacing: '.13em', textTransform: 'uppercase', color: C.muted,
    }}>Swee Choon · TRIBE v2</div>
    {dots.map((src, i) => {
      const q = easeOut(t, at(9) + 0.15 + i * 0.14, at(9) + 0.9 + i * 0.14);
      return <Img key={src} src={staticFile(src)} style={{
        position: 'absolute', left: 24 + q * 150, top: 110 + i * 92,
        width: 54, height: 96, objectFit: 'cover', borderRadius: 6,
        opacity: q * (1 - easeOut(t, at(9) + 1.4, at(9) + 2.1)),
        transform: `translateX(${(1 - q) * -110}px) scale(${0.82 + q * 0.18})`,
        border: '1px solid rgba(255,138,42,.55)',
        boxShadow: '0 8px 20px rgba(0,0,0,.4)',
      }} />;
    })}
  </div>;
};

const ResponsePanel: React.FC<{t: number; appear: number}> = ({t, appear}) => {
  const p = easeOut(t, appear, appear + 0.5);
  const draw = easeIO(t, at(9) + 1.5, at(10) + 0.1);
  const showMuted = t >= at(10) - 0.1;
  const mutedDraw = easeIO(t, at(10) + 0.2, at(10) + 1.6);
  const zeroY = 12 + ((RANGE[1] - 0) / (RANGE[1] - RANGE[0])) * (GRAPH.h - 24);
  return <div style={{
    position: 'absolute', left: MAP.response.x, top: MAP.response.y, width: MAP.response.w, height: MAP.response.h,
    borderRadius: 14, background: C.surface, border: `1px solid ${C.rule}`,
    opacity: p, overflow: 'hidden', boxShadow: '0 18px 50px rgba(0,0,0,.45)', fontFamily,
  }}>
    <div style={{
      position: 'absolute', left: 22, top: 20, fontSize: 18, fontWeight: 500,
      letterSpacing: '.12em', textTransform: 'uppercase', color: C.muted,
    }}>Response</div>
    <div style={{position: 'absolute', left: GRAPH.x, top: GRAPH.y - 6, width: GRAPH.w, height: GRAPH.h}}>
      <div style={{
        position: 'absolute', left: 8, top: zeroY, width: GRAPH.w - 16,
        borderTop: '1px dashed rgba(160,166,175,.44)',
      }} />
      <Graph
        values={withSound}
        comparison={showMuted ? withoutSound : undefined}
        from={0}
        to={shared - 1}
        range={RANGE}
        width={GRAPH.w}
        height={GRAPH.h}
        progress={draw}
        comparisonProgress={mutedDraw}
        color={C.hot}
        comparisonColor="rgba(236,237,239,.72)"
        comparisonDashed
        axes={false}
        label="journey-response"
        strokeWidth={4}
      />
    </div>
    <div style={{
      position: 'absolute', left: 22, bottom: 18, display: 'flex', gap: 26,
      alignItems: 'center', fontSize: 18, color: C.text,
    }}>
      <span style={{display: 'flex', alignItems: 'center', gap: 9}}>
        <span style={{width: 28, height: 3, background: C.hot, borderRadius: 2}} /> with sound
      </span>
      <span style={{
        display: 'flex', alignItems: 'center', gap: 9,
        opacity: showMuted ? 1 : 0.25,
      }}>
        <span style={{width: 28, borderTop: '3px dashed rgba(236,237,239,.72)'}} /> on mute
      </span>
    </div>
  </div>;
};

const ProgressCutIn: React.FC<{
  from: number; duration: number; src: string; sourceStart: number; title: string;
  mark: {x: number; y: number; w: number; h: number};
}> = ({from, duration, src, sourceStart, title, mark}) => {
  const total = sec(duration);
  return <Sequence from={sec(from - SCENES.journey.start)} durationInFrames={total}>
    <CutInBody total={total} src={src} sourceStart={sourceStart} title={title} mark={mark} />
  </Sequence>;
};

const CutInBody: React.FC<{
  total: number; src: string; sourceStart: number; title: string;
  mark: {x: number; y: number; w: number; h: number};
}> = ({total, src, sourceStart, title, mark}) => {
  const frame = useCurrentFrame();
  const rampRate = 0.65 + 0.85 * Math.min(1, frame / total);
  const fade = interpolate(frame, [0, 8, total - 8, total], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  return <AbsoluteFill style={{opacity: fade}}>
    <AbsoluteFill style={{background: 'rgba(0,0,0,.80)'}} />
    <ScreenWindow
      title={title}
      x={330} y={120} w={1260} h={840}
      src={src}
      kind="video"
      sourceW={2940} sourceH={1912}
      sourceStart={sourceStart}
      rate={rampRate}
      base={{x: 900, y: 490, w: 1280, h: 840}}
      cams={[{at: 0, cx: 1540, cy: 890, zoom: 1}, {at: 0.8, cx: 1540, cy: 900, zoom: 1.04}]}
    >
      <SourceMark x={mark.x} y={mark.y} w={mark.w} h={mark.h} />
    </ScreenWindow>
  </AbsoluteFill>;
};

const captions = [
  {from: at(4), to: at(5), text: 'Straight to storage', sub: 'S3 · the file never touches our server'},
  {from: at(5), to: at(6), text: 'Serverless', sub: 'CloudFront + Lambda'},
  {from: at(6), to: at(7), text: 'Checked before the GPU', sub: 'queue → length + price'},
  {from: at(7), to: at(8), text: 'Idle: $0 / hour', sub: 'the GPU is off'},
  {from: at(8), to: at(9), text: 'Scale to zero', sub: 'one GPU, only while there is work'},
  {from: at(9), to: at(10), text: 'Frames into a brain', sub: 'TRIBE v2 · Swee Choon'},
  {from: at(10), to: at(11), text: 'Twice: sound and mute', sub: 'two response curves'},
  {from: at(11), to: at(12), text: 'Saved, then charged', sub: 'a failed job gets its credit back'},
  {from: at(12), to: at(13), text: 'The report writes itself', sub: 'Bedrock · Claude'},
  {from: at(13), to: SCENES.journey.end, text: 'Back to zero', sub: 'the GPU switches itself off'},
];

export const Journey: React.FC = () => {
  const frame = useCurrentFrame();
  const t = SCENES.journey.start + frame / FPS;
  const local = frame / FPS;
  const cam = cameraAt(t);
  const dot = dotAt(t);
  const glow = easeIO(t, JOURNEY.gpuWake, JOURNEY.gpuWake + 1.2)
    * (1 - easeIO(t, at(13) + 0.2, at(13) + 1.1));
  const pulse = Math.max(0, Math.sin(clamp((t - at(5) - 0.35) / 0.7) * Math.PI))
    * (t > at(5) ? 1 : 0);
  const websiteDim = t > at(6) ? 0.25 : 0;
  const charged = t >= at(11) + 3.4;
  const refund = easeOut(t, at(11) + 4.2, at(11) + 5.2);
  const reportWrite = easeOut(t, at(12) + 0.4, at(12) + 2.6);
  const caption = captions.find((c) => t >= c.from && t < c.to) ?? captions[captions.length - 1];
  const capFade = easeOut(local, 0.15, 0.55);
  const gpuLabel = t >= JOURNEY.gpuWake && t < at(13) + 1.2 ? 'SageMaker · managed' : 'idle: $0 / hour';

  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 48% 52%, #0B0C0F 0%, #000 74%)', fontFamily, color: C.text, overflow: 'hidden'}}>
    <div style={{
      position: 'absolute',
      left: 960 - cam.cx,
      top: 540 - cam.cy,
      width: MAP_W,
      height: MAP_H,
    }}>
      <svg width={MAP_W} height={MAP_H} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
        <defs>
          <marker id="map-arrow" viewBox="0 0 10 10" refX="8" refY="5"
            markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#4B515A" />
          </marker>
        </defs>
        <Path d="M 400 385 L 548 385" opacity={1} />
        <Path d="M 860 385 L 1068 385" opacity={easeOut(t, at(6) - 0.4, at(6) + 0.2)} />
        <Path d="M 250 470 L 250 645 L 548 645" opacity={easeOut(t, at(5) - 0.4, at(5) + 0.2)} dashed />
        <Path d="M 880 645 L 1040 645 L 1040 470" opacity={easeOut(t, at(5) - 0.1, at(5) + 0.5)} dashed />
        <Path d="M 1380 385 L 1548 385" opacity={1} />
        <Path d="M 1860 385 L 2048 385" opacity={1} />
        <Path d="M 1860 470 L 1710 548" opacity={0.7} dashed />
        <Path d="M 2400 490 L 2400 548" opacity={easeOut(t, at(9) - 0.3, at(9) + 0.2)} />
        <Path d="M 2680 790 L 2748 790" opacity={easeOut(t, at(9) + 1.2, at(9) + 1.6)} />
        <Path d="M 2400 300 L 2888 300" opacity={easeOut(t, at(12) - 0.3, at(12) + 0.3)} />
        <Path d="M 1710 730 C 1280 920, 620 900, 250 482" opacity={refund * 0.42} dashed color="#FF8A2A" />
      </svg>

      <Card {...MAP.browser} appear={JOURNEY.start + 0.05} t={t} />
      <Card {...MAP.s3} appear={JOURNEY.start + 0.3} t={t} />

      <Card {...MAP.website} appear={at(5) - 0.45} t={t} glow={pulse} dim={websiteDim} />
      <Card {...MAP.queue} appear={at(6) - 0.35} t={t}
        glow={t > at(6) + 0.5 && t < at(6) + 3 ? 0.7 : 0}>
        {t > at(6) + 3.2 ? <div style={{
          marginTop: 8, fontSize: 17, color: C.hot, letterSpacing: '-0.005em',
        }}>job arrived</div> : null}
      </Card>

      <Card {...MAP.check} appear={at(6) + 0.7} t={t}>
        <div style={{
          display: 'flex', gap: 15, marginTop: 12, fontSize: 17, color: C.muted,
          opacity: easeOut(t, at(6) + 3.9, at(6) + 4.2),
        }}>
          <span>length <span style={{color: C.hot}}>✓</span></span>
          <span style={{
            opacity: easeOut(t, at(6) + 4.5, at(6) + 4.8),
          }}>price <span style={{color: C.hot}}>✓</span></span>
        </div>
      </Card>

      <Card {...MAP.gpu} appear={at(7) - 0.3} t={t} glow={glow} title="GPU" label={gpuLabel}>
        <div style={{
          marginTop: 10, fontSize: 17, color: glow > 0.1 ? C.hot : C.muted,
          letterSpacing: '-0.005em',
        }}>
          {glow > 0.1 ? 'one instance, warm' : 'nothing running'}
        </div>
      </Card>

      <Card {...MAP.credits} appear={at(11) - 0.45} t={t} glow={charged ? 0.5 : 0}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, marginTop: 12,
          fontSize: 19, fontWeight: 600, color: charged ? C.hot : C.muted,
        }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            height: 30, padding: '0 12px', borderRadius: 999, boxSizing: 'border-box',
            border: `1px solid ${charged ? C.hot : 'rgba(160,166,175,.45)'}`,
            background: charged ? 'rgba(255,138,42,.12)' : 'transparent',
          }}>{charged ? 'charged' : 'held'}</span>
          <span style={{fontSize: 17, fontWeight: 400, color: C.muted}}>{charged ? '1 min' : 'not charged yet'}</span>
        </div>
      </Card>

      <BrainPanel t={t} appear={at(9) - 0.4} />
      <ResponsePanel t={t} appear={at(9) + 1.1} />

      <Card {...MAP.report} appear={at(12) - 0.4} t={t} glow={0.35}>
        <div style={{marginTop: 12, display: 'flex', flexDirection: 'column', gap: 7}}>
          {[0.85, 0.62, 0.72].map((width, i) => <div key={i} style={{
            width: `${width * reportWrite * 100}%`, height: 5, borderRadius: 3,
            background: i === 0 ? 'rgba(255,138,42,.72)' : 'rgba(236,237,239,.32)',
          }} />)}
        </div>
      </Card>

      {refund > 0.01 && <div style={{
        position: 'absolute', left: 940, top: 900, fontFamily, fontSize: 18, fontWeight: 500,
        color: 'rgba(255,138,42,.72)', letterSpacing: '.02em', opacity: refund,
      }}>refund path</div>}

      {dot && <div style={{
        position: 'absolute', left: dot.x, top: dot.y, width: 16, height: 16,
        borderRadius: 999, background: C.hot, transform: 'translate(-50%, -50%)',
        boxShadow: '0 0 0 7px rgba(255,138,42,.12), 0 0 26px rgba(255,138,42,.8)',
        zIndex: 10,
      }} />}
    </div>

    {caption && <div style={{
      position: 'absolute', left: 110, top: 96, opacity: capFade,
      maxWidth: 1180,
    }}>
      <div style={{fontSize: 64, lineHeight: 1.02, fontWeight: 600, letterSpacing: '-0.045em', color: C.text}}>
        {caption.text}
      </div>
      <div style={{marginTop: 11, fontSize: 23, fontWeight: 500, letterSpacing: '-0.005em', color: C.muted}}>
        {caption.sub}
      </div>
    </div>}

    <div style={{
      position: 'absolute', right: 110, top: 100, fontFamily, fontSize: 17, fontWeight: 500,
      letterSpacing: '.13em', textTransform: 'uppercase', color: 'rgba(160,166,175,.65)',
    }}>While it waits</div>

    <ProgressCutIn
      from={at(6) + 3.3} duration={2.1} src="walkthrough/s5_waiting.mp4" sourceStart={4.0}
      title="NeuroLens — Upload"
      mark={{x: 1004, y: 680, w: 820, h: 58}}
    />
    <ProgressCutIn
      from={at(8) + 0.45} duration={2.3} src="walkthrough/s5_progress.mp4" sourceStart={18.0}
      title="NeuroLens — Waiting for the GPU"
      mark={{x: 1004, y: 790, w: 820, h: 58}}
    />
    <ProgressCutIn
      from={at(11) + 2.1} duration={2.2} src="walkthrough/s5_progress.mp4" sourceStart={470.0}
      title="NeuroLens — Processing"
      mark={{x: 1004, y: 1016, w: 820, h: 80}}
    />
  </AbsoluteFill>;
};
