import React from 'react';
import {OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {MacChrome} from '../walkthrough/MacWindow';
import {cropAt, fit} from './camera';
import {
  CAMS, FPS, SEGMENTS, SOURCE_H, SOURCE_W, STAGE, Segment, TAKE_SRC, sec,
} from './timing';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const TakeVideo: React.FC<{segment: Segment}> = ({segment}) => {
  const frame = useCurrentFrame();
  const absolute = frame + segment.from;
  const t = absolute / FPS;
  const crop = cropAt(CAMS, t);
  const {scale, offX, offY} = fit(crop, STAGE);
  const opacity = Math.min(
    clamp((absolute - segment.from) / 8),
    clamp((segment.to - absolute) / segment.fade),
  );

  return <div style={{position: 'absolute', inset: 0, opacity, background: '#000'}}>
    <div style={{
      position: 'absolute',
      left: offX - crop.x * scale,
      top: offY - crop.y * scale,
      width: SOURCE_W,
      height: SOURCE_H,
      transform: `scale(${scale})`,
      transformOrigin: '0 0',
    }}>
      <OffthreadVideo
        src={staticFile(TAKE_SRC[segment.take])}
        trimBefore={sec(segment.sourceStart)}
        playbackRate={segment.rate}
        muted
        style={{position: 'absolute', left: 0, top: 0, width: SOURCE_W, height: SOURCE_H}}
      />
    </div>
  </div>;
};

export const Stage: React.FC = () => (
  <div style={{position: 'absolute', left: STAGE.x, top: STAGE.y, width: STAGE.w, height: STAGE.h}}>
    <MacChrome title="NeuroLens">
      {SEGMENTS.map((segment, i) => (
        <Sequence
          key={`${segment.take}-${i}`}
          from={segment.from}
          durationInFrames={segment.to - segment.from + 1}
        >
          <TakeVideo segment={segment} />
        </Sequence>
      ))}
    </MacChrome>
  </div>
);
