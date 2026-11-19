import React from 'react';
import {Sequence, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {at} from '../../edit/Graph';
import {BrainView, PhoneClip, Signpost, Stage, clamp, easeInOut, easeOut, lerp} from '../common';
import {MUTED, OVERALL, RANGE, TO} from '../result/timeline';
import {
  BEDROCK_IN,
  CLIP_JUMP,
  CLOSE_START,
  CURSOR_FROM,
  CURSOR_TO,
  DURATION,
  REPORT,
  ROW_IN,
  SHOT,
  SOUND_OFF_IN,
  VIEW_QUESTION_IN,
} from './timeline';

const fmt = (s: number) => `0:${String(s).padStart(2, '0')}`;
const range = `${fmt(REPORT.moment.start)}–${fmt(REPORT.moment.end)}`;

// Short gists lifted word for word from the real report text, shown dim: the voice says more than the screen.
const GIST = {onScreen: 'Black-and-white footage and old photos', brain: 'overall stays well above typical'};
for (const [key, source] of [[GIST.onScreen, REPORT.moment.what_happens], [GIST.brain, REPORT.moment.brain_response]]) {
  if (!source.includes(key)) throw new Error(`report gist "${key}" is not in the real report text`);
}

const CARD = {x: 160, y: 100, w: 1600, h: 740};
// The full report sentence stays in the small three-row card; the big Do card says it in one line.
const SUGGESTION_HEADLINE = 'Add a one-second shot of her face';

// 10: the clip and the brain keep moving, with a thin live response line under them.
const PHONE = {x: 330, y: 170, w: 330};
const PHONE_H = (PHONE.w * 16) / 9;
const BRAIN = {x: 980, y: 120, size: 480};
const LINE = {x: 890, y: 614, w: 780, h: 190};
const TITLE_Y = 78;

// 11: the calm wide product shot, with the small wordmark under it.
const CLOSE_CLIP = {x: 560, y: 150, w: 300};
const CLOSE_BRAIN = {x: 940, y: 176, size: 480};

const Row: React.FC<{label: string; text: string; top: number; opacity: number; hot?: boolean; dim?: boolean; size?: number}> = ({
  label, text, top, opacity, hot, dim, size = 40,
}) => (
  <div style={{
    position: 'absolute', left: 60, right: 60, top, display: 'flex', gap: 40, alignItems: 'flex-start', opacity,
    transform: `translateY(${(1 - opacity) * 14}px)`,
  }}>
    <div style={{width: 250, flexShrink: 0, fontFamily, fontSize: 44, fontWeight: 600, letterSpacing: '-.01em', color: hot ? C.hot : C.muted, opacity: dim ? 0.8 : 1, paddingTop: 2}}>{label}</div>
    <div style={{flex: 1, fontFamily, fontSize: size, lineHeight: 1.3, fontWeight: 500, letterSpacing: '-.01em', color: hot ? C.text : C.muted, opacity: dim ? 0.7 : 1}}>{text}</div>
  </div>
);

const PAD_X = 8;
const PAD_Y = 20;
const LINE_W = LINE.w - PAD_X * 2;
const LINE_H = LINE.h - PAD_Y * 2;
const xAt = (second: number) => PAD_X + (clamp(second, 0, TO) / TO) * LINE_W;
const yAt = (value: number) => PAD_Y + ((RANGE[1] - value) / (RANGE[1] - RANGE[0])) * LINE_H;
const linePath = (values: number[], from: number, to: number) => {
  const start = clamp(from, 0, TO);
  const end = clamp(to, 0, TO);
  const points: Array<[number, number]> = [[xAt(start), yAt(at(values, start))]];
  for (let second = Math.floor(start) + 1; second <= Math.floor(end); second += 1) {
    points.push([xAt(second), yAt(values[second])]);
  }
  points.push([xAt(end), yAt(at(values, end))]);
  return points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
};

const LiveLine: React.FC<{second: number; weak: number}> = ({second, weak}) => {
  const end = clamp(second, 0, TO);
  const playX = xAt(end);
  const playY = yAt(at(OVERALL, end));
  return (
    <svg width={LINE.w} height={LINE.h} style={{position: 'absolute', left: LINE.x, top: LINE.y, overflow: 'visible'}} aria-hidden>
      <path d={linePath(OVERALL, 0, TO)} fill="none" stroke={C.hot} strokeOpacity={0.16} strokeWidth={3} />
      <path d={linePath(MUTED, 0, TO)} fill="none" stroke="#6F7E99" strokeOpacity={0.22} strokeWidth={2.5} strokeDasharray="10 9" />
      {weak > 0.001 ? (
        <>
          <rect
            x={xAt(CURSOR_FROM)} y={PAD_Y} width={xAt(CURSOR_TO) - xAt(CURSOR_FROM)} height={LINE_H}
            rx={14} fill={C.hot} opacity={0.07 * weak}
          />
          <path
            d={linePath(MUTED, CURSOR_FROM, CURSOR_TO)} fill="none" stroke={C.hot}
            strokeOpacity={0.22 * weak} strokeWidth={13} strokeLinecap="round"
          />
          <path
            d={linePath(MUTED, CURSOR_FROM, CURSOR_TO)} fill="none" stroke="#B4C2D6"
            strokeOpacity={0.35 + 0.65 * weak} strokeWidth={4} strokeDasharray="10 9" strokeLinecap="round"
          />
        </>
      ) : null}
      {end > 0.01 ? (
        <>
          <path d={linePath(OVERALL, 0, end)} fill="none" stroke={C.hot} strokeWidth={5} strokeLinecap="round" strokeLinejoin="round" />
          <path d={linePath(MUTED, 0, end)} fill="none" stroke="#6F7E99" strokeWidth={3} strokeDasharray="10 9" strokeLinecap="round" strokeLinejoin="round" />
          <line x1={playX} x2={playX} y1={PAD_Y} y2={LINE.h - PAD_Y} stroke={C.text} strokeOpacity={0.28} strokeWidth={2} />
          <circle cx={playX} cy={playY} r={7} fill={C.hot} stroke="#0B0C0F" strokeWidth={3} />
        </>
      ) : null}
    </svg>
  );
};

const PlayIcon: React.FC = () => (
  <svg width={38} height={38} viewBox="0 0 38 38" aria-hidden>
    <path d="M11 7 L31 19 L11 31 Z" fill={C.muted} />
  </svg>
);

const ViewCounter: React.FC<{opacity: number; question: number}> = ({opacity, question}) => (
  <div style={{
    position: 'absolute', left: PHONE.x + PHONE.w / 2, top: PHONE.y + PHONE_H + 24, transform: 'translateX(-50%)',
    display: 'flex', alignItems: 'center', gap: 16, opacity, fontFamily, whiteSpace: 'nowrap',
  }}>
    <PlayIcon />
    <span style={{position: 'relative', fontSize: 42, fontWeight: 600, color: C.text, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em', lineHeight: 1}}>
      <span style={{filter: 'blur(8px)', opacity: 0.5}}>1.2M</span>
      <span style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.hot, fontWeight: 700, opacity: question}}>?</span>
    </span>
  </div>
);

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();

  // 9: the report card appears with the first words (9a) and its three rows of one moment follow the
  // voice, then the Do line alone (9b). Only the Do line is written out in full.
  const cardIn = easeOut(frame, SHOT['9a'].start + 2, SHOT['9a'].start + 20);
  const rowsIn = (n: number) => easeOut(frame, ROW_IN[n], ROW_IN[n] + 20);
  const rowsQuiet = easeInOut(frame, SHOT['9b'].start + 4, SHOT['9b'].start + 22);
  const cardOut = 1 - easeOut(frame, SHOT['10a'].start - 6, SHOT['10a'].start + 6);
  const bedrock = easeOut(frame, BEDROCK_IN, BEDROCK_IN + 14) * (1 - easeOut(frame, SHOT['9b'].start, SHOT['9b'].start + 12));
  const push = 1 + 0.04 * easeInOut(frame, SHOT['9b'].start + 10, SHOT['9b'].end);
  // 9.2 names the sound-off finding, so the card says it plainly under the Do line.
  const soundOff = easeOut(frame, SOUND_OFF_IN, SOUND_OFF_IN + 14);

  // 10: the clip plays beside the brain; on 10.2 it jumps to 0:08, where the sound-off response is weak.
  const limitIn = easeOut(frame, SHOT['10a'].start + 2, SHOT['10a'].start + 22);
  const limitOut = 1 - easeOut(frame, SHOT['11'].start - 8, SHOT['11'].start + 4);
  const clipSecond = frame < CLIP_JUMP
    ? (frame - SHOT['10a'].start) / 30
    : CURSOR_FROM + (frame - CLIP_JUMP) / 30;
  const weak = easeOut(frame, CLIP_JUMP, CLIP_JUMP + 18) * (0.85 + 0.15 * Math.sin(frame / 14));
  const question = easeOut(frame, VIEW_QUESTION_IN, VIEW_QUESTION_IN + 14);

  // 11: a calm wide product shot, a slow push-in, then the existing fade to black.
  const homeIn = easeOut(frame, CLOSE_START + 2, CLOSE_START + 22);
  const closeSecond = (frame - CLOSE_START) / 30;
  const closePush = lerp(1, 1.045, easeInOut(frame, CLOSE_START, DURATION));
  const fadeBlack = easeInOut(frame, DURATION - 30, DURATION);

  return (
    <Stage>
      {cardOut > 0.01 ? (
        <div style={{opacity: cardIn * cardOut, position: 'absolute', inset: 0}}>
          <div style={{
            position: 'absolute', left: CARD.x, top: CARD.y, width: CARD.w, height: CARD.h, borderRadius: 28, boxSizing: 'border-box',
            background: `rgba(17,19,22,${0.92 * (1 - rowsQuiet)})`, border: `1.5px solid rgba(36,38,43,${1 - rowsQuiet})`,
            boxShadow: `0 30px 80px rgba(0,0,0,${0.55 * (1 - rowsQuiet)})`,
          }}>
            <div style={{position: 'absolute', left: 60, top: 56, fontFamily, fontSize: 44, fontWeight: 600, color: C.muted, opacity: 1 - rowsQuiet, fontVariantNumeric: 'tabular-nums'}}>{range}</div>
            <div style={{position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, opacity: 1}}>
              <Row label="On screen" text={GIST.onScreen} top={170} dim opacity={rowsIn(0) * (1 - rowsQuiet)} />
              <Row label="Brain" text={GIST.brain} top={300} dim opacity={rowsIn(1) * (1 - rowsQuiet)} />
              <Row label="Do" text={REPORT.moment.suggestion ?? ''} hot top={440} opacity={rowsIn(2) * (1 - rowsQuiet)} />
            </div>
            <div style={{
              position: 'absolute', left: 0, right: 0, bottom: 46, margin: '0 auto', maxWidth: 1190, textAlign: 'center',
              fontFamily, fontSize: 38, lineHeight: 1.35, fontWeight: 500, letterSpacing: '-.005em', color: C.muted, opacity: soundOff,
            }}>
              the response nearly vanishes with sound off over the old photos
            </div>
          </div>
          <Signpost text="Claude · Amazon Bedrock" y={CARD.y + CARD.h + 36} opacity={bedrock} />
          <div style={{
            position: 'absolute', left: 200, right: 200, top: 0, bottom: 0, display: 'flex', flexDirection: 'column', alignItems: 'center',
            justifyContent: 'center', gap: 36, textAlign: 'center', opacity: rowsQuiet, transform: `scale(${push})`,
          }}>
            <div style={{fontFamily, fontSize: 56, fontWeight: 600, color: C.hot, letterSpacing: '-.01em'}}>Do</div>
            <div style={{fontFamily, fontSize: 72, lineHeight: 1.2, fontWeight: 600, letterSpacing: '-.025em', color: C.text}}>{SUGGESTION_HEADLINE}</div>
          </div>
        </div>
      ) : null}

      {limitIn * limitOut > 0.01 ? (
        <div style={{position: 'absolute', inset: 0, opacity: limitIn * limitOut}}>
          <div style={{
            position: 'absolute', left: 0, right: 0, top: TITLE_Y, textAlign: 'center', fontFamily, fontSize: 36,
            fontWeight: 600, letterSpacing: '.01em', color: C.muted,
          }}>Perception, not views</div>
          <Sequence from={SHOT['10a'].start} durationInFrames={CLIP_JUMP - SHOT['10a'].start} layout="none">
            <PhoneClip {...PHONE} from={0} time={clamp(clipSecond, 0, TO)} />
          </Sequence>
          <Sequence from={CLIP_JUMP} durationInFrames={SHOT['11'].start + 8 - CLIP_JUMP} layout="none">
            <PhoneClip {...PHONE} from={CURSOR_FROM} time={clamp(clipSecond, 0, TO)} />
          </Sequence>
          <ViewCounter opacity={1} question={question} />
          <BrainView x={BRAIN.x} y={BRAIN.y} size={BRAIN.size} second={clamp(clipSecond, 0, TO)} />
          <LiveLine second={clipSecond} weak={weak} />
        </div>
      ) : null}

      {homeIn > 0.01 ? (
        <div style={{position: 'absolute', inset: 0, opacity: homeIn, transform: `scale(${closePush})`, transformOrigin: 'center center'}}>
          <Sequence from={CLOSE_START} durationInFrames={DURATION - CLOSE_START} layout="none">
            <PhoneClip {...CLOSE_CLIP} from={0} />
          </Sequence>
          <BrainView x={CLOSE_BRAIN.x} y={CLOSE_BRAIN.y} size={CLOSE_BRAIN.size} second={clamp(closeSecond, 0, TO)} />
          <div style={{position: 'absolute', left: 0, right: 0, top: 724, textAlign: 'center'}}>
            <div style={{fontFamily, fontSize: 50, fontWeight: 700, letterSpacing: '-.03em', color: C.text}}>NeuroLens</div>
            <div style={{marginTop: 14, fontFamily, fontSize: 32, fontWeight: 500, letterSpacing: '-.005em', color: C.muted}}>Watch a brain watch your video.</div>
          </div>
        </div>
      ) : null}

      <div style={{position: 'absolute', inset: 0, background: '#000', opacity: fadeBlack, pointerEvents: 'none'}} />
    </Stage>
  );
};
