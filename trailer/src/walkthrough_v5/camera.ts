import {Easing} from 'remotion';
import {BAR, Cam, PAGE_H, PAGE_TOP, SOURCE_H, SOURCE_W} from './timing';

const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export type Crop = {x: number; y: number; w: number; h: number};

export const cameraAt = (cams: Cam[], t: number): Cam => {
  let previous = cams[0];
  for (let i = 1; i < cams.length; i++) {
    const next = cams[i];
    if (t < next.at) break;
    const glide = next.glide ?? 1.2;
    if (t <= next.at + glide) {
      const p = Easing.inOut(Easing.cubic)(clamp((t - next.at) / glide, 0, 1));
      return {
        at: next.at,
        cx: lerp(previous.cx, next.cx, p),
        cy: lerp(previous.cy, next.cy, p),
        zoom: Math.exp(lerp(Math.log(previous.zoom), Math.log(next.zoom), p)),
      };
    }
    previous = next;
  }
  return previous;
};

const cropFor = (cam: Cam): Crop => {
  const w = SOURCE_W / cam.zoom;
  const h = PAGE_H / cam.zoom;
  return {
    x: clamp(cam.cx - w / 2, 0, Math.max(0, SOURCE_W - w)),
    y: clamp(cam.cy - h / 2, PAGE_TOP, Math.max(PAGE_TOP, PAGE_TOP + PAGE_H - h)),
    w,
    h,
  };
};

export const cropAt = (cams: Cam[], t: number) => cropFor(cameraAt(cams, t));

export type WindowBox = {x: number; y: number; w: number; h: number};

export const fit = (crop: Crop, box: WindowBox) => {
  const contentH = box.h - BAR;
  const scale = Math.max(box.w / crop.w, contentH / crop.h);
  return {
    scale,
    offX: (box.w - crop.w * scale) / 2,
    offY: (contentH - crop.h * scale) / 2,
  };
};

export const project = (
  source: {x: number; y: number},
  crop: Crop,
  box: WindowBox,
) => {
  const {scale, offX, offY} = fit(crop, box);
  return {
    x: box.x + offX + (source.x - crop.x) * scale,
    y: box.y + BAR + offY + (source.y - crop.y) * scale,
  };
};
