import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {DIAGRAM, FPS} from './timeline';

const easeOut = (t: number, a: number, b: number) =>
  interpolate(t, [a, b], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

const Box: React.FC<{
  x: number; y: number; w: number; h: number; title: string; label?: string;
  t: number; appear: number; glow?: number;
}> = ({x, y, w, h, title, label, t, appear, glow = 0}) => {
  const p = easeOut(t, appear, appear + 0.45);
  const hot = glow > 0.01;
  return <div style={{
    position: 'absolute', left: x, top: y, width: w, height: h,
    opacity: p, transform: `translateY(${(1 - p) * 10}px)`,
    background: C.surface, borderRadius: 14,
    border: `1px solid ${hot ? C.hot : C.rule}`,
    boxShadow: hot
      ? `0 0 0 1px ${C.hot}, 0 0 34px rgba(255,138,42,.30)`
      : '0 12px 30px rgba(0,0,0,.32)',
    display: 'flex', flexDirection: 'column', justifyContent: 'center',
    alignItems: label ? 'flex-start' : 'center', padding: '0 18px',
    fontFamily, boxSizing: 'border-box',
  }}>
    <div style={{
      fontSize: 21, lineHeight: 1.1, fontWeight: 600, letterSpacing: '-0.025em',
      color: C.text, textAlign: label ? 'left' : 'center',
    }}>{title}</div>
    {label ? <div style={{
      marginTop: 8, fontSize: 11.5, lineHeight: 1.35, color: C.muted,
      letterSpacing: '-0.005em',
    }}>{label}</div> : null}
  </div>;
};

const Arrow: React.FC<{d: string; opacity: number}> = ({d, opacity}) =>
  <path d={d} fill="none" stroke="#4B515A" strokeWidth={1.2}
    markerEnd="url(#arrow)" opacity={opacity} />;

export const Diagram: React.FC = () => {
  const f = useCurrentFrame();
  const t = DIAGRAM.start + f / FPS;

  const viewerA = DIAGRAM.start + 0.25;
  const websiteA = DIAGRAM.start + 1.8;
  const queueA = DIAGRAM.line16 + 0.2;
  const gpuA = DIAGRAM.line16 + 1.15;
  const creditsA = DIAGRAM.line17 + 0.1;
  const reportA = DIAGRAM.line18 + 0.15;
  const watchdogA = DIAGRAM.line19 + 0.1;

  const pulsePhase = (t - (DIAGRAM.line15 + 0.15)) / 0.55;
  const pulse = pulsePhase > 0 && pulsePhase < 1 ? Math.sin(pulsePhase * Math.PI) : 0;

  const travel = interpolate(t, [DIAGRAM.line16 + 2.1, DIAGRAM.line16 + 3.0], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  const glow = interpolate(t, [DIAGRAM.line16 + 2.0, DIAGRAM.line16 + 4.9], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  }) * (t >= DIAGRAM.line16 + 1.2 ? 1 : 0);

  const minus = easeOut(t, DIAGRAM.line17 + 1.3, DIAGRAM.line17 + 1.7)
    * (1 - easeOut(t, DIAGRAM.line17 + 3.0, DIAGRAM.line17 + 3.3));
  const plus = easeOut(t, DIAGRAM.line17 + 3.0, DIAGRAM.line17 + 3.4);
  const check = easeOut(t, DIAGRAM.line19 + 2.0, DIAGRAM.line19 + 2.6);
  const frameP = easeOut(t, DIAGRAM.line20 + 0.1, DIAGRAM.line20 + 0.9);
  const settle = easeOut(t, DIAGRAM.line20 + 0.1, DIAGRAM.line20 + 1.0);

  const watchLine = easeOut(t, DIAGRAM.line19 + 0.6, DIAGRAM.line19 + 1.1);

  return <AbsoluteFill style={{background: '#000', fontFamily, color: C.text}}>
    <div style={{
      position: 'absolute', left: 70, top: 300, width: 1780, height: 470,
      borderRadius: 20, border: `1px solid ${C.rule}`, opacity: frameP,
      boxShadow: '0 30px 90px rgba(0,0,0,.6)',
    }} />
    <div style={{
      position: 'absolute', left: 102, top: 318, opacity: frameP,
      fontSize: 26, fontWeight: 600, letterSpacing: '-0.03em', color: C.text,
    }}>Terraform</div>
    <div style={{
      position: 'absolute', left: 102, top: 716, opacity: frameP,
      fontSize: 13, color: C.muted, letterSpacing: '-0.005em',
    }}>one command up, one command down</div>

    <div style={{
      position: 'absolute', inset: 0,
      transform: `scale(${1 - settle * 0.055}) translateY(${-settle * 8}px)`,
      transformOrigin: '50% 50%',
    }}>
      <svg width={1920} height={1080} style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#4B515A" />
          </marker>
        </defs>
        <Arrow d="M 390 435 L 460 435" opacity={easeOut(t, websiteA, websiteA + 0.4)} />
        <Arrow d="M 710 435 L 780 435" opacity={easeOut(t, queueA, queueA + 0.4)} />
        <Arrow d="M 1010 435 L 1080 435" opacity={easeOut(t, gpuA, gpuA + 0.4)} />
        <Arrow d="M 1330 435 L 1400 435" opacity={easeOut(t, reportA, reportA + 0.4)} />
        <Arrow d="M 840 590 L 840 532 L 1205 532 L 1205 490" opacity={watchLine} />
      </svg>

      <Box x={140} y={380} w={250} h={110} title="Viewer's browser"
        t={t} appear={viewerA} />
      <Box x={460} y={380} w={250} h={110} title="Website"
        label="CloudFront + Lambda (Flask)" t={t} appear={websiteA}
        glow={pulse * 0.8} />
      <Box x={780} y={380} w={230} h={110} title="Queue" label="SQS"
        t={t} appear={queueA} />
      <Box x={1080} y={380} w={250} h={110} title="GPU"
        label="SageMaker, scales to zero" t={t} appear={gpuA} glow={glow} />
      <Box x={1400} y={380} w={250} h={110} title="Report"
        label={'Bedrock \u00b7 Claude'} t={t} appear={reportA} />
      <Box x={1080} y={590} w={250} h={100} title="Credits"
        label="Aurora Serverless" t={t} appear={creditsA} />
      <Box x={700} y={590} w={280} h={100} title="Watchdog"
        label="EventBridge, every 10 min" t={t} appear={watchdogA} />

      <div style={{
        position: 'absolute', left: 1010 + travel * 70, top: 435,
        width: 10, height: 10, borderRadius: 5, background: C.hot,
        transform: 'translate(-50%, -50%)', opacity: travel > 0 && travel < 1 ? 1 : 0,
        boxShadow: '0 0 14px rgba(255,138,42,.7)',
      }} />

      <div style={{
        position: 'absolute', left: 1360, top: 606, fontFamily, fontWeight: 500,
        fontSize: 14, color: C.hot, fontVariantNumeric: 'tabular-nums',
        opacity: minus, transform: `translateY(${(1 - minus) * 5}px)`,
      }}>{'\u22121 min'}</div>
      <div style={{
        position: 'absolute', left: 1360, top: 640, fontFamily, fontWeight: 500,
        fontSize: 14, color: C.hot, fontVariantNumeric: 'tabular-nums',
        opacity: plus, transform: `translateY(${(1 - plus) * 5}px)`,
      }}>{'+1 min \u21ba'}</div>

      <svg width={1920} height={1080} style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
        <polyline points="936,614 946,624 964,600" fill="none" stroke={C.hot}
          strokeWidth={3} strokeLinecap="round" strokeLinejoin="round"
          strokeDasharray={44} strokeDashoffset={44 * (1 - check)} opacity={check > 0 ? 1 : 0} />
      </svg>
    </div>
  </AbsoluteFill>;
};
