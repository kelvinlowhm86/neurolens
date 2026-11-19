import React from 'react';
import {Img, staticFile, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {
  BrainView, CreditStrip, Cursor, PhoneClip, Ring, Signpost, Stage, clamp, easeBack, easeInOut, easeOut, lerp,
} from '../common';
import {BRAIN_IN, BROWSER_CREDIT_SECONDS, BROWSER_HOLD_SHARE, BROWSER_SECONDS, CREDIT_POP, DROP, FILE_NAME, FPS, HELD, SHOT} from './timeline';

const FRAME_ART = 'walkthrough/swee/frames/f0.jpg';
// Credit is time, never money; decimal minutes when it is not whole ("1.5 min").
const minutes = (seconds: number) => `${Number((seconds / 60).toFixed(2))} min`;
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

const ZONE = {x: 360, y: 280, w: 1200, h: 520};
const ROW = {x: 510, y: 300, w: 900, h: 440};

const Card: React.FC<{box: typeof ZONE; opacity: number; dashed?: boolean; glow?: number; children?: React.ReactNode}> = ({
  box, opacity, dashed, glow = 0, children,
}) => (
  <div style={{
    position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, borderRadius: 28, boxSizing: 'border-box',
    background: 'rgba(17,19,22,.92)', border: dashed ? `3px dashed ${glow > 0.5 ? 'rgba(255,138,42,.8)' : '#343943'}` : `1.5px solid ${C.rule}`,
    boxShadow: `0 30px 80px rgba(0,0,0,.55), 0 0 ${60 * glow}px rgba(255,138,42,${0.25 * glow})`, opacity,
  }}>{children}</div>
);

const UploadIcon: React.FC<{size: number; colour: string}> = ({size, colour}) => (
  <svg width={size} height={size} viewBox="0 0 28 28" fill="none" stroke={colour} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M14 19V5M8 11l6-6 6 6M5 19v4h18v-4" />
  </svg>
);

const FileChip: React.FC<{x: number; y: number; opacity: number; scale: number}> = ({x, y, opacity, scale}) => (
  <div style={{
    position: 'absolute', left: x, top: y, height: 132, padding: '0 40px 0 22px', display: 'flex', alignItems: 'center', gap: 24,
    borderRadius: 22, background: '#1C1E22', border: '1.5px solid rgba(255,158,84,.7)', boxSizing: 'border-box',
    boxShadow: '0 24px 60px rgba(0,0,0,.6), 0 0 44px rgba(255,138,42,.22)', opacity,
    transform: `translate(-50%, -50%) scale(${scale})`, fontFamily, fontSize: 44, fontWeight: 600, color: C.text, whiteSpace: 'nowrap', zIndex: 20,
  }}>
    <Img src={staticFile(FRAME_ART)} style={{width: 76, height: 96, objectFit: 'cover', borderRadius: 10}} />
    {FILE_NAME}
  </div>
);

export const Intro: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;

  // 1a-2a: the clip, then the clip beside its brain; 2a keeps the TikTok on screen.
  const shrink = easeInOut(frame, SHOT['1b'].start, SHOT['1b'].start + 18);
  const baseW = lerp(560, 320, shrink);
  const baseH = (baseW * 16) / 9;
  const baseX = lerp(680, 480, shrink);
  const baseY = lerp(36, 540 - baseH / 2, shrink);
  const clipCx = baseX + baseW / 2;
  const clipCy = baseY + baseH / 2;
  const clipOpacity = 1 - easeOut(frame, SHOT['2b'].start + 8, SHOT['2b'].start + 14);
  const brainIn = easeOut(frame, BRAIN_IN, BRAIN_IN + 30) * (1 - easeOut(frame, SHOT['2a'].start, SHOT['2a'].start + 18));

  // 2b: the clip shrinks into the file chip, the drop zone appears, and the chip is dragged in.
  const morph = easeInOut(frame, SHOT['2b'].start, SHOT['2b'].start + 10);
  const clipW = lerp(baseW, 90, morph);
  const clipH = (clipW * 16) / 9;
  const clipX = clipCx - clipW / 2;
  const clipY = clipCy - clipH / 2;
  const zoneIn = easeOut(frame, SHOT['2b'].start, SHOT['2b'].start + 10);
  const zoneOut = 1 - easeOut(frame, DROP + 20, DROP + 30);
  const zoneDim = 1 - 0.7 * easeOut(frame, DROP - 14, DROP + 6);
  const chipIn = easeOut(frame, SHOT['2b'].start + 2, SHOT['2b'].start + 12);
  const drag = easeInOut(frame, SHOT['2b'].start + 8, DROP);
  const dropped = frame >= DROP;
  const chipPos = {x: lerp(clipCx, 960, drag), y: lerp(clipCy, 590, drag)};
  const chipScale = frame < DROP ? lerp(1.04, 1, drag) : 1 - 0.04 * Math.sin(clamp((frame - DROP) / 10) * Math.PI);
  const chipOut = 1 - easeOut(frame, DROP + 20, DROP + 30);
  const ring = clamp((frame - DROP) / 18);
  const cursorX = chipPos.x + 36;
  const cursorY = chipPos.y + 34;
  const cursorOpacity = easeOut(frame, SHOT['2b'].start + 2, SHOT['2b'].start + 12) * (1 - easeOut(frame, DROP + 14, DROP + 30));

  // 2b-2c: the file's length and credit, then the held slice.
  const rowIn = easeOut(frame, DROP + 22, DROP + 38);
  const rowDim = 1 - 0.45 * easeOut(frame, SHOT['2c'].start, SHOT['2c'].start + 18);
  const creditPop = easeBack(frame, CREDIT_POP, CREDIT_POP + 24);
  const stripIn = easeOut(frame, SHOT['2c'].start, SHOT['2c'].start + 16);
  const held = easeInOut(frame, HELD, HELD + 40);
  const heldWord = easeOut(frame, HELD + 4, HELD + 20);

  return (
    <Stage>
      {clipOpacity > 0.01 ? (
        <PhoneClip x={clipX} y={clipY} w={clipW} from={0} opacity={clipOpacity} />
      ) : null}
      <BrainView x={880} y={280} size={560} second={t} opacity={brainIn} />

      {zoneIn * zoneOut > 0.01 ? (
        <Card box={ZONE} opacity={zoneIn * zoneOut} dashed glow={dropped ? 0 : drag}>
          <div style={{
            position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            gap: 22, opacity: zoneDim, transform: `scale(${lerp(0.97, 1, zoneIn)})`,
          }}>
            <UploadIcon size={110} colour={C.muted} />
            <div style={{fontFamily, fontSize: 68, fontWeight: 600, letterSpacing: '-.02em', color: C.text}}>Drop videos or click to choose</div>
            <div style={{fontFamily, fontSize: 44, color: C.muted}}>MP4, MOV or WebM</div>
          </div>
        </Card>
      ) : null}

      {chipIn * chipOut > 0.01 ? <FileChip x={chipPos.x} y={chipPos.y} opacity={chipIn * chipOut} scale={chipScale} /> : null}
      <Ring x={960} y={590} p={ring} />
      {cursorOpacity > 0.01 ? <Cursor x={cursorX} y={cursorY} opacity={cursorOpacity} pressed={false} /> : null}

      {rowIn > 0.01 ? (
        <Card box={ROW} opacity={rowIn * rowDim}>
          <Img src={staticFile(FRAME_ART)} style={{
            position: 'absolute', left: 60, top: 60, width: 198, height: 320, objectFit: 'cover', borderRadius: 20,
            border: '1.5px solid rgba(255,255,255,.16)',
          }} />
          <div style={{position: 'absolute', left: 316, top: 56, fontFamily, fontSize: 44, fontWeight: 500, color: C.muted, whiteSpace: 'nowrap'}}>{FILE_NAME}</div>
          <div style={{position: 'absolute', left: 316, top: 128, fontFamily, fontSize: 64, fontWeight: 600, color: C.text, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.02em'}}>{clock(BROWSER_SECONDS)}</div>
          <div style={{
            position: 'absolute', left: 316, top: 232, display: 'flex', alignItems: 'baseline', gap: 18, fontFamily, whiteSpace: 'nowrap',
            opacity: clamp(creditPop * 2), transform: `scale(${lerp(0.9, 1, creditPop)})`, transformOrigin: 'left center',
          }}>
            <span style={{fontSize: 40, fontWeight: 500, color: C.muted}}>uses</span>
            <span style={{fontSize: 96, fontWeight: 600, letterSpacing: '-.04em', color: C.hot, fontVariantNumeric: 'tabular-nums', lineHeight: 1}}>{minutes(BROWSER_CREDIT_SECONDS)}</span>
            <span style={{fontSize: 40, fontWeight: 500, color: C.muted}}>credit</span>
          </div>
        </Card>
      ) : null}

      <Signpost text="held" y={808} opacity={heldWord} />
      <CreditStrip opacity={stripIn} held={held} holdShare={BROWSER_HOLD_SHARE} status={held > 0.5 ? 'held' : ''} lit={held > 0.5} />
    </Stage>
  );
};
