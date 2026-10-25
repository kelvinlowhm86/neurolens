/** Binary layout and colouring mirror static/brain_view.js. No per-shot normalisation. */
export const PREDICTION_VERTICES = 20484;
// config.json response.colour_range: the app's fixed comparison scale.
export const COLOUR_RANGE = 0.5;
const HEAT = [[0, [.50, 0, 0]], [.35, [.90, .10, .04]], [.65, [1, .50, .04]], [.88, [1, .85, .15]], [1, [1, 1, .85]]] as const;
export type BrainData = {
  positions: Float32Array; faces: Uint32Array; mask: Uint8Array;
  parents: Uint32Array; base: Float32Array; rows: Float32Array; count: number;
};
export const parseBrain = (meshBuffer: ArrayBuffer, brainBuffer: ArrayBuffer): BrainData => {
  const dv = new DataView(meshBuffer);
  const vertices = dv.getUint32(0, true), facesCount = dv.getUint32(4, true);
  if (meshBuffer.byteLength !== 8 + vertices * 25 + facesCount * 12) throw new Error('Invalid brain mesh length');
  let offset = 8;
  const positions = new Float32Array(meshBuffer.slice(offset, offset + vertices * 12)); offset += vertices * 12;
  const faces = new Uint32Array(meshBuffer.slice(offset, offset + facesCount * 12)); offset += facesCount * 12;
  const mask = new Uint8Array(meshBuffer.slice(offset, offset + vertices)); offset += vertices;
  const sulc = new Float32Array(meshBuffer.slice(offset, offset + vertices * 4)); offset += vertices * 4;
  const parents = new Uint32Array(meshBuffer.slice(offset));
  if (faces.some((v) => v >= vertices) || parents.some((v) => v >= PREDICTION_VERTICES)) throw new Error('Invalid brain mesh index');
  const base = new Float32Array(vertices);
  let cx = 0, cy = 0, cz = 0;
  for (let i = 0; i < vertices; i++) {
    cx += positions[i * 3]; cy += positions[i * 3 + 1]; cz += positions[i * 3 + 2];
    base[i] = (.80 + .17 * (.5 - .5 * sulc[i])) * (mask[i] ? .82 : 1);
  }
  for (let i = 0; i < vertices; i++) {
    positions[i * 3] -= cx / vertices; positions[i * 3 + 1] -= cy / vertices; positions[i * 3 + 2] -= cz / vertices;
  }
  if (!brainBuffer.byteLength || brainBuffer.byteLength % (PREDICTION_VERTICES * 2)) throw new Error('Invalid prediction row length');
  const half = new DataView(brainBuffer), rows = new Float32Array(brainBuffer.byteLength / 2);
  for (let i = 0; i < rows.length; i++) {
    const h = half.getUint16(i * 2, true), exponent = (h >> 10) & 31, mantissa = h & 1023;
    const sign = h & 32768 ? -1 : 1;
    const value = exponent === 0 ? sign * mantissa * 2 ** -24 : exponent === 31 ? NaN : sign * (1 + mantissa / 1024) * 2 ** (exponent - 15);
    if (!Number.isFinite(value)) throw new Error('Nonfinite prediction value');
    rows[i] = value;
  }
  return {positions, faces, mask, parents, base, rows, count: rows.length / PREDICTION_VERTICES};
};
export const paintBrain = (data: BrainData, second: number, colours: Float32Array, activity: Float32Array) => {
  const time = Math.max(0, Math.min(data.count - 1, second));
  const a = Math.floor(time), b = Math.min(a + 1, data.count - 1), u = time - a;
  for (let i = 0; i < data.base.length; i++) {
    const pa = data.parents[i * 2], pb = data.parents[i * 2 + 1];
    const va = data.rows[a * PREDICTION_VERTICES + pa] * (1 - u) + data.rows[b * PREDICTION_VERTICES + pa] * u;
    const vb = data.rows[a * PREDICTION_VERTICES + pb] * (1 - u) + data.rows[b * PREDICTION_VERTICES + pb] * u;
    const shade = Math.max(0, Math.min(1, ((va + vb) / 2) / COLOUR_RANGE));
    const weight = data.mask[i] ? 0 : Math.max(0, Math.min(1, (shade - .04) / .12));
    activity[i] = data.mask[i] ? 0 : Math.max(0, (shade - .58) / .42);
    let stop = 1;
    while (stop < HEAT.length - 1 && shade > HEAT[stop][0]) stop++;
    const [lo, lc] = HEAT[stop - 1], [hi, hc] = HEAT[stop];
    const blend = (shade - lo) / (hi - lo);
    for (let k = 0; k < 3; k++) {
      const heat = lc[k] + (hc[k] - lc[k]) * blend;
      colours[i * 3 + k] = data.base[i] + (heat - data.base[i]) * weight;
    }
  }
};
