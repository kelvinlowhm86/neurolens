import {Easing} from 'remotion';
import {BAR, Cam, PAGE_H, PAGE_TOP, SOURCE_W} from './timing';

const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export type Crop = {x: number; y: number; w: number; h: number; contain: boolean};

const cropFor = (cam: Cam): Crop => {
  const w = cam.w ?? SOURCE_W / (cam.zoom ?? 1);
  const h = cam.h ?? PAGE_H / (cam.zoom ?? 1);
  return {
    x: clamp(cam.cx - w / 2, 0, Math.max(0, SOURCE_W - w)),
    y: clamp(cam.cy - h / 2, PAGE_TOP, Math.max(PAGE_TOP, PAGE_TOP + PAGE_H - h)),
    w,
    h,
    contain: cam.contain ?? false,
  };
};

// Interpolate the visible source rectangle rather than a zoom factor, so a
// framing can change shape (e.g. the flat group section) as well as scale.
export const cameraAt = (cams: Cam[], t: number): Crop => {
  let previous = cams[0];
  for (let i = 1; i < cams.length; i++) {
    const next = cams[i];
    if (t < next.at) break;
    const glide = next.glide ?? 1.2;
    if (t <= next.at + glide) {
      const p = Easing.inOut(Easing.cubic)(clamp((t - next.at) / glide, 0, 1));
      const a = cropFor(previous);
      const b = cropFor(next);
      return {
        x: lerp(a.x, b.x, p),
        y: lerp(a.y, b.y, p),
        w: lerp(a.w, b.w, p),
        h: lerp(a.h, b.h, p),
        contain: a.contain || b.contain,
      };
    }
    previous = next;
  }
  return cropFor(previous);
};

export const cropAt = (cams: Cam[], t: number) => cameraAt(cams, t);

export type WindowBox = {x: number; y: number; w: number; h: number};

export const fit = (crop: Crop, box: WindowBox) => {
  const contentH = box.h - BAR;
  const scale = crop.contain
    ? Math.min(box.w / crop.w, contentH / crop.h)
    : Math.max(box.w / crop.w, contentH / crop.h);
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
