import * as THREE from '/static/three/three.module.min.js';
import { OrbitControls } from '/static/three/OrbitControls.js';

export const VERTICES = 20484;
const MESH_URL = '/static/brain/fsaverage6_pial.bin';
const HEAD_URL = '/static/brain/head.bin';
export const UNAVAILABLE = 'The 3D view is not available for this video.';
const HEAT = [[0, [0.50, 0.0, 0.0]], [0.35, [0.90, 0.10, 0.04]], [0.65, [1, 0.50, 0.04]], [0.88, [1, 0.85, 0.15]], [1, [1, 1, 0.85]]];
const SWEEP_MS = 700;

function cubicBezierEase(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sampleX = t => ((ax * t + bx) * t + cx) * t;
  const sampleY = t => ((ay * t + by) * t + cy) * t;
  return x => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let lo = 0, hi = 1, t = x;
    for (let i = 0; i < 24; i++) {
      const at = sampleX(t);
      if (Math.abs(at - x) < 1e-5) break;
      if (at < x) lo = t; else hi = t;
      t = (lo + hi) / 2;
    }
    return sampleY(t);
  };
}

const SWEEP_EASE = cubicBezierEase(0.2, 0.7, 0.2, 1);

function heat(t, out) {
  for (let i = 1; i < HEAT.length; i++) {
    if (t <= HEAT[i][0]) {
      const [a, ca] = HEAT[i - 1], [b, cb] = HEAT[i], u = (t - a) / (b - a);
      for (let k = 0; k < 3; k++) out[k] = ca[k] + (cb[k] - ca[k]) * u;
      return;
    }
  }
  out[0] = HEAT[HEAT.length - 1][1][0];
  out[1] = HEAT[HEAT.length - 1][1][1];
  out[2] = HEAT[HEAT.length - 1][1][2];
}

function parseMesh(buffer) {
  const dv = new DataView(buffer);
  const V = dv.getUint32(0, true), F = dv.getUint32(4, true);
  const expected = 8 + V * 12 + F * 12 + V + V * 4 + V * 8;
  if (buffer.byteLength !== expected) throw new Error('wrong mesh length');
  let o = 8;
  const xyz = new Float32Array(dv.buffer.slice(o, o + V * 12)); o += V * 12;
  const faces = new Uint32Array(dv.buffer.slice(o, o + F * 12)); o += F * 12;
  const noCortex = new Uint8Array(dv.buffer.slice(o, o + V)); o += V;
  const sulc = new Float32Array(dv.buffer.slice(o, o + V * 4)); o += V * 4;
  const parents = new Uint32Array(dv.buffer.slice(o, o + V * 8));
  for (let i = 0; i < faces.length; i++) if (faces[i] >= V) throw new Error('face index out of range');
  for (let i = 0; i < parents.length; i++) if (parents[i] >= VERTICES) throw new Error('parent index out of range');
  return { V, F, xyz, faces, noCortex, sulc, parents };
}

function parseHead(buffer) {
  const dv = new DataView(buffer);
  const V = dv.getUint32(0, true), F = dv.getUint32(4, true);
  const expected = 8 + V * 12 + F * 12;
  if (buffer.byteLength !== expected) throw new Error('wrong head length');
  const xyz = new Float32Array(dv.buffer.slice(8, 8 + V * 12));
  const faces = new Uint32Array(dv.buffer.slice(8 + V * 12, expected));
  for (let i = 0; i < faces.length; i++) if (faces[i] >= V) throw new Error('head face out of range');
  return { V, F, xyz, faces };
}

let float16Lookup = null;
function float16Table() {
  if (float16Lookup) return float16Lookup;
  const table = new Float32Array(65536);
  for (let h = 0; h < 65536; h++) {
    const sign = h & 0x8000 ? -1 : 1;
    const exp = (h >> 10) & 0x1f;
    const mant = h & 0x3ff;
    if (exp === 0) table[h] = sign * mant * 2 ** -24;
    else if (exp === 31) table[h] = mant ? NaN : sign * Infinity;
    else table[h] = sign * (1 + mant / 1024) * 2 ** (exp - 15);
  }
  float16Lookup = table;
  return table;
}

export function decodeBrain(buffer) {
  const bytesPerRow = VERTICES * 2;
  if (!buffer.byteLength || buffer.byteLength % bytesPerRow !== 0) return null;
  const count = buffer.byteLength / bytesPerRow;
  const view = new DataView(buffer);
  const table = float16Table();
  const rows = new Float32Array(count * VERTICES);
  for (let i = 0; i < rows.length; i++) rows[i] = table[view.getUint16(i * 2, true)];
  return { rows, count };
}

export function createBrainView(host, message) {
  if (!host || !message) return { show() {}, setSecond() {}, clear() {}, setColourRange() {} };

  let rows = null, rowCount = 0, second = 0, range = null;
  let ready = false, meshFailed = false, meshPromise = null;
  let renderer, scene, camera, controls, geometry, colourAttr, baseColour, parents, cortexMask;
  let skeleton = null;
  let sweep = null, swept = false;
  let lastPaint = -1;
  const reducedMotion = (() => {
    try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  })();
  const sweepPlane = reducedMotion ? null : new THREE.Plane(new THREE.Vector3(0, 0, 1), 1e6);

  function showSkeleton() {
    if (skeleton || !host) return;
    skeleton = document.createElement('div');
    skeleton.className = 'brain-skeleton';
    skeleton.setAttribute('aria-hidden', 'true');
    skeleton.innerHTML = '<svg viewBox="0 0 400 250" fill="none" stroke="rgba(232,234,237,.20)" stroke-width="1.2"><path d="M70,140 C52,96 82,52 140,40 C186,22 250,24 300,46 C346,66 368,104 362,140 C358,170 336,186 310,190 C300,206 280,214 258,210 C236,224 196,226 172,212 C140,214 112,204 96,186 C78,176 72,160 70,140 Z"/><path d="M120,70 C150,90 160,120 150,150 M190,46 C200,80 210,110 200,150 M250,44 C252,80 262,110 280,130 M110,172 C150,160 200,162 250,180 M300,70 C310,100 330,120 350,130" stroke="rgba(232,234,237,.12)" stroke-dasharray="3 5"/></svg>';
    host.append(skeleton);
  }

  function hideSkeleton() {
    if (!skeleton) return;
    skeleton.remove();
    skeleton = null;
  }

  function setMessage(text) {
    message.textContent = text;
    message.hidden = !text;
    renderer?.domElement?.toggleAttribute('hidden', !!text);
    if (!text) resize();
  }

  function resize() {
    if (!ready) return;
    const width = host.clientWidth, height = host.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    fitCamera();
  }

  function fitCamera() {
    if (!ready) return;
    const sphere = geometry.boundingSphere;
    if (!sphere) return;
    const vf = THREE.MathUtils.degToRad(camera.fov);
    const hf = 2 * Math.atan(Math.tan(vf / 2) * camera.aspect);
    const distance = (sphere.radius * 1.08) / Math.sin(Math.min(vf, hf) / 2);
    const dir = new THREE.Vector3(-1, 1, 0.42).normalize();
    camera.position.copy(sphere.center).addScaledVector(dir, distance);
    controls.target.copy(sphere.center);
    controls.update();
  }

  function startSweep() {
    if (swept) return;
    swept = true;
    if (!sweepPlane || !geometry) return;
    const sphere = geometry.boundingSphere;
    if (!sphere) return;
    // Front to back as seen from the camera: the plane keeps the part behind the moving cut.
    const normal = new THREE.Vector3();
    camera.getWorldDirection(normal);
    normal.negate();
    const centre = normal.dot(sphere.center);
    sweep = { normal, from: centre + sphere.radius, to: centre - sphere.radius, started: performance.now() };
    sweepPlane.normal.copy(normal);
    sweepPlane.constant = -sweep.from;
  }

  function updateSweep(now) {
    if (!sweep) return;
    const progress = Math.min(1, (now - sweep.started) / SWEEP_MS);
    sweepPlane.constant = -(sweep.from + (sweep.to - sweep.from) * SWEEP_EASE(progress));
    if (progress >= 1) endSweep();
  }

  function endSweep() {
    if (!sweep) return;
    sweepPlane.constant = 1e6;
    sweep = null;
  }

  function paint(time) {
    if (!ready || !rows || !range) return;
    if (Math.abs(time - lastPaint) < 0.02) return;
    lastPaint = time;
    const t = Math.max(0, Math.min(rowCount - 1, time));
    const a0 = Math.floor(t), b = Math.min(a0 + 1, rowCount - 1), u = t - a0;
    const values = colourAttr.array, tmp = [0, 0, 0];
    const heatRange = range;
    for (let i = 0; i < geometry.attributes.position.count; i++) {
      const parentA = parents[2 * i], parentB = parents[2 * i + 1];
      const va = rows[a0 * VERTICES + parentA] + (rows[b * VERTICES + parentA] - rows[a0 * VERTICES + parentA]) * u;
      const vb = rows[a0 * VERTICES + parentB] + (rows[b * VERTICES + parentB] - rows[a0 * VERTICES + parentB]) * u;
      const value = (va + vb) / 2;
      let r = baseColour[3 * i], g = baseColour[3 * i + 1], bl = baseColour[3 * i + 2];
      if (!cortexMask[i]) {
        const shade = Math.max(0, Math.min(1, value / heatRange));
        const w = Math.min(1, Math.max(0, (shade - 0.04) / 0.12));  // a short fade, so a hot area has a clear dark-red edge
        if (w > 0) {
          heat(shade, tmp);
          r += (tmp[0] - r) * w; g += (tmp[1] - g) * w; bl += (tmp[2] - bl) * w;
        }
      }
      values[3 * i] = r; values[3 * i + 1] = g; values[3 * i + 2] = bl;
    }
    colourAttr.needsUpdate = true;
  }

  function applyState() {
    if (!ready) {
      if (meshFailed) { setMessage(UNAVAILABLE); return; }
      if (range && rows) { setMessage('Loading the 3D view…'); ensureMesh(); }
      else setMessage(UNAVAILABLE);
      return;
    }
    if (!range || !rows) { setMessage(UNAVAILABLE); return; }
    setMessage('');
    paint(second);
  }

  async function build() {
    showSkeleton();
    const [meshBuffer, headBuffer] = await Promise.all([
      fetch(MESH_URL).then(res => {
        if (!res.ok) throw new Error(`mesh HTTP ${res.status}`);
        return res.arrayBuffer();
      }),
      fetch(HEAD_URL).then(res => res.ok ? res.arrayBuffer() : null).catch(() => null),
    ]);
    const mesh = parseMesh(meshBuffer);
    cortexMask = mesh.noCortex;
    parents = mesh.parents;

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.localClippingEnabled = !!sweepPlane;
    if ('outputColorSpace' in renderer) renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.prepend(renderer.domElement);

    scene = new THREE.Scene();
    geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(mesh.xyz, 3));
    geometry.setIndex(new THREE.BufferAttribute(mesh.faces, 1));
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();

    baseColour = new Float32Array(mesh.V * 3);
    for (let i = 0; i < mesh.V; i++) {
      const shade = 0.5 - 0.5 * mesh.sulc[i];
      const base = (0.80 + 0.17 * shade) * (mesh.noCortex[i] ? 0.82 : 1);
      baseColour[3 * i] = base; baseColour[3 * i + 1] = base; baseColour[3 * i + 2] = base;
    }
    colourAttr = new THREE.BufferAttribute(new Float32Array(mesh.V * 3), 3);
    colourAttr.setUsage(THREE.DynamicDrawUsage);
    colourAttr.array.set(baseColour);
    geometry.setAttribute('vcol', colourAttr);

    const material = new THREE.ShaderMaterial({
      clippingPlanes: sweepPlane ? [sweepPlane] : [],
      vertexShader: '#include <clipping_planes_pars_vertex>\nattribute vec3 vcol; varying vec3 vColor; varying vec3 vNormal; void main(){ vColor = vcol; vNormal = normalize(normalMatrix * normal); vec4 mvPosition = modelViewMatrix * vec4(position, 1.0); gl_Position = projectionMatrix * mvPosition;\n#include <clipping_planes_vertex>\n}',
      fragmentShader: '#include <clipping_planes_pars_fragment>\nvarying vec3 vColor; varying vec3 vNormal; void main(){\n#include <clipping_planes_fragment>\nfloat d = max(0.0, dot(normalize(vNormal), normalize(vec3(0.35, 0.45, 0.9)))); gl_FragColor = vec4(vColor * (0.65 + 0.35 * d), 1.0); }',
    });
    scene.add(new THREE.Mesh(geometry, material));

    const sphere = geometry.boundingSphere;
    camera = new THREE.PerspectiveCamera(28, 1, 0.1, 3000);
    camera.up.set(0, 0, 1);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enableZoom = false;
    controls.enablePan = false;
    controls.autoRotate = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    controls.autoRotateSpeed = 0.5;

    if (headBuffer) {
      try {
        const head = parseHead(headBuffer);
        const hg = new THREE.BufferGeometry();
        hg.setAttribute('position', new THREE.BufferAttribute(head.xyz, 3));
        hg.setIndex(new THREE.BufferAttribute(head.faces, 1));
        hg.computeVertexNormals();
        const hm = new THREE.Mesh(hg, new THREE.ShaderMaterial({
          transparent: true, depthWrite: false, side: THREE.DoubleSide,
          clippingPlanes: sweepPlane ? [sweepPlane] : [],
          vertexShader: '#include <clipping_planes_pars_vertex>\nvarying vec3 n; varying vec3 v; void main(){ vec4 mvPosition = modelViewMatrix*vec4(position,1.); n = normalize(normalMatrix*normal); v = normalize(-mvPosition.xyz); gl_Position = projectionMatrix*mvPosition;\n#include <clipping_planes_vertex>\n}',
          fragmentShader: '#include <clipping_planes_pars_fragment>\nvarying vec3 n; varying vec3 v; void main(){\n#include <clipping_planes_fragment>\nfloat f = pow(1.0-abs(dot(normalize(n),normalize(v))), 3.0); gl_FragColor = vec4(0.42,0.55,0.78, f*0.35); }',
        }));
        hm.renderOrder = 2;
        scene.add(hm);
      } catch (e) { /* the head is a nicety */ }
    }

    ready = true;
    hideSkeleton();
    camera.aspect = Math.max(1, host.clientWidth) / Math.max(1, host.clientHeight);
    camera.updateProjectionMatrix();
    fitCamera();
    new ResizeObserver(resize).observe(host);
    resize();
    startSweep();
    applyState();
    renderer.setAnimationLoop(() => {
      if (!host.clientWidth) return;  // the player is hidden; stop drawing until it shows again
      updateSweep(performance.now());
      controls.update();
      renderer.render(scene, camera);
    });
  }

  function ensureMesh() {
    if (!meshPromise) {
      meshPromise = build().catch(error => {
        meshFailed = true;
        ready = false;
        hideSkeleton();
        setMessage(UNAVAILABLE);
        console.warn('3D view:', error);
      });
    }
    return meshPromise;
  }

  return {
    show(nextRows, count) {
      rows = nextRows; rowCount = count;
      second = Math.max(0, Math.min(count - 1, second));
      lastPaint = -1;
      applyState();
    },
    setSecond(value) {
      second = Number.isFinite(value) ? value : 0;
      if (ready && rows) paint(second);
    },
    clear(text) {
      rows = null; rowCount = 0;
      setMessage(text || UNAVAILABLE);
    },
    setColourRange(value) {
      range = Number.isFinite(value) && value > 0 ? value : null;
      if (!range) rows = null;
      lastPaint = -1;
      applyState();
    },
  };
}
