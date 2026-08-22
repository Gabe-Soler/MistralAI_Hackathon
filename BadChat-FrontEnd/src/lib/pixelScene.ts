/**
 * The hero's backdrop: a pixel-art sunset, generated rather than shipped as an
 * image, so it re-renders at whatever grid the viewport calls for.
 *
 * Everything is described in normalised coordinates — `u` across the width, `v`
 * down the height — with circles measured in *height* units so they stay round
 * on any aspect. One scene definition, any grid size.
 *
 * The output is palette *indices*, one byte per cell, not RGB. That is what
 * makes the cursor interaction cheap: nudging a pixel one step up the ramp is
 * an array lookup (see LIGHTER), and warping the scene is a resample of a byte
 * array rather than of pixels.
 */

/** Sky, top of frame → horizon. Neighbours are dithered together as it descends. */
const SKY_RAMP = [
  '#C4173F',
  '#D42A41',
  '#E4423A',
  '#F26129',
  '#FD8227',
  '#FF9E42',
  '#FFB967',
  '#FFD290',
  '#FFE7BC',
]

const SKY_N = SKY_RAMP.length
const SUN = SKY_N
const CLOUD = SKY_N + 1
const CLOUD_LIT = SKY_N + 2
const RANGE_FAR = SKY_N + 3
const RANGE_NEAR = SKY_N + 4
const DUNE_FAR = SKY_N + 5
const DUNE_MID = SKY_N + 6
const DUNE_NEAR = SKY_N + 7
const GROUND = SKY_N + 8

export const PALETTE = [
  ...SKY_RAMP,
  '#FFF6E0', // sun
  '#FFCE8A', // cloud
  '#FFE5B6', // cloud, lit top edge
  '#E2634A', // far range
  '#CB4238', // near range
  '#E8A25C', // dunes, far band
  '#EFB771', // dunes, middle band
  '#F5CD92', // dunes, near band
  '#F9DEB0', // foreground sand
]

/** Palette as flat RGB triples, for writing straight into an ImageData. */
export const PALETTE_RGB = new Uint8Array(PALETTE.length * 3)
PALETTE.forEach((hex, i) => {
  const n = parseInt(hex.slice(1), 16)
  PALETTE_RGB[i * 3] = (n >> 16) & 255
  PALETTE_RGB[i * 3 + 1] = (n >> 8) & 255
  PALETTE_RGB[i * 3 + 2] = n & 255
})

/**
 * One step toward the light end of whatever ramp a colour belongs to. The
 * cursor uses it to scorch the pixels directly under the cat without needing
 * to know what it is standing on.
 */
export const LIGHTER = new Uint8Array(PALETTE.length)
for (let i = 0; i < SKY_N; i++) LIGHTER[i] = Math.min(i + 1, SKY_N - 1)
LIGHTER[SUN] = SUN
LIGHTER[CLOUD] = CLOUD_LIT
LIGHTER[CLOUD_LIT] = CLOUD_LIT
LIGHTER[RANGE_NEAR] = RANGE_FAR
LIGHTER[RANGE_FAR] = DUNE_FAR
LIGHTER[DUNE_FAR] = DUNE_MID
LIGHTER[DUNE_MID] = DUNE_NEAR
LIGHTER[DUNE_NEAR] = GROUND
LIGHTER[GROUND] = GROUND

/**
 * 4×4 ordered (Bayer) dither. Every soft edge in the scene — the sky ramp, the
 * band boundaries, the shading under each dune crest — is this threshold
 * compared against a coverage fraction, which is where the crosshatch texture
 * of the whole picture comes from.
 */
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]
const dither = (x: number, y: number) => (BAYER[(y & 3) * 4 + (x & 3)] + 0.5) / 16

/** Where the flat sand starts; every ridge is measured up from here. */
const HORIZON = 0.7

const SUN_U = 0.68
const SUN_V = 0.42
const SUN_R = 0.2

/** Triangular peak: height above the horizon at `u`, in v units. */
interface Peak {
  c: number
  hw: number
  h: number
}
const ridge = (u: number, base: number, peaks: Peak[]) =>
  peaks.reduce(
    (h, p) => Math.max(h, p.h * Math.max(0, 1 - Math.abs(u - p.c) / p.hw)),
    base,
  )

const FAR_PEAKS: Peak[] = [
  { c: 0.72, hw: 0.24, h: 0.15 },
  { c: 0.24, hw: 0.2, h: 0.08 },
  { c: 0.46, hw: 0.18, h: 0.05 },
]
const NEAR_PEAKS: Peak[] = [
  { c: 0.12, hw: 0.26, h: 0.07 },
  { c: 0.52, hw: 0.22, h: 0.05 },
]

/** Smooth mound, for the dune bands. */
const mound = (u: number, c: number, w: number, h: number) => {
  const t = (u - c) / w
  return t * t > 1 ? 0 : h * (1 - t * t) * (1 - t * t)
}

interface Band {
  /** Resting height of the band's crest, as a fraction of the frame. */
  top: number
  colour: number
  mounds: [c: number, w: number, h: number][]
}
const DUNES: Band[] = [
  { top: 0.755, colour: DUNE_FAR, mounds: [[0.3, 0.22, 0.05], [0.78, 0.2, 0.045]] },
  { top: 0.815, colour: DUNE_MID, mounds: [[0.16, 0.2, 0.05], [0.62, 0.26, 0.04]] },
  { top: 0.885, colour: DUNE_NEAR, mounds: [[0.44, 0.3, 0.045], [0.92, 0.18, 0.04]] },
]

/** Cloud lobes: [u, v, half-width in u, half-height in v]. */
type Lobe = [number, number, number, number]
const CLOUDS: Lobe[][] = [
  [[0.62, 0.07, 0.09, 0.018], [0.68, 0.055, 0.05, 0.014], [0.77, 0.075, 0.05, 0.011]],
  [[0.15, 0.2, 0.08, 0.016], [0.2, 0.185, 0.045, 0.013]],
  [[0.36, 0.24, 0.06, 0.012]],
  [[0.33, 0.44, 0.09, 0.016], [0.28, 0.425, 0.05, 0.012]],
  [[0.63, 0.55, 0.08, 0.014], [0.69, 0.535, 0.04, 0.011]],
]

const inLobes = (u: number, v: number, lobes: Lobe[]) =>
  lobes.some(([cu, cv, rw, rh]) => {
    const du = (u - cu) / rw
    const dv = (v - cv) / rh
    return du * du + dv * dv <= 1
  })

/** True where `v` has fallen past `top`, with a dithered rather than hard edge. */
const past = (v: number, top: number, feather: number, x: number, y: number) =>
  (v - top) / feather > dither(x, y)

/**
 * Fills `out` with one palette index per cell, painting far to near: sky, sun,
 * clouds, the two ranges, three dune bands, foreground sand.
 */
export function buildScene(w: number, h: number): Uint8Array {
  const out = new Uint8Array(w * h)
  const ar = w / h
  // Round on wide screens, tamed on narrow ones where a height-relative sun
  // would swallow the frame.
  const sunR = SUN_R * Math.min(1, Math.max(0.6, ar / 1.7))
  const dv = 1 / h

  for (let y = 0; y < h; y++) {
    const v = (y + 0.5) / h
    for (let x = 0; x < w; x++) {
      const u = (x + 0.5) / w

      const sunDu = (u - SUN_U) * ar
      const sunDv = v - SUN_V
      const sunD = Math.sqrt(sunDu * sunDu + sunDv * sunDv)

      // Sky: position along the ramp, brightened toward the sun, dithered
      // between the two entries it falls between.
      const fall = Math.pow(Math.min(1, v / HORIZON), 0.85) * (SKY_N - 1)
      const glow = 2.6 * Math.exp(-Math.pow(sunD / (sunR * 2.4), 2))
      const t = Math.min(SKY_N - 1.001, fall + glow)
      const base = Math.floor(t)
      let c = t - base > dither(x, y) ? base + 1 : base

      if (sunD <= sunR) c = SUN

      for (const lobes of CLOUDS) {
        if (!inLobes(u, v, lobes)) continue
        c = inLobes(u, v - 2 * dv, lobes) ? CLOUD : CLOUD_LIT
        break
      }

      const far = HORIZON - ridge(u, 0.03, FAR_PEAKS)
      if (past(v, far, 0.006, x, y)) c = RANGE_FAR
      const near = HORIZON - ridge(u, 0.01, NEAR_PEAKS)
      if (past(v, near, 0.006, x, y)) c = RANGE_NEAR

      for (const band of DUNES) {
        const top =
          band.top - band.mounds.reduce((m, [bc, bw, bh]) => m + mound(u, bc, bw, bh), 0)
        if (!past(v, top, 0.008, x, y)) continue
        c = band.colour
        // Sunlit crest: the face just under the ridge dithers one step light,
        // fading out as it drops into the band.
        const lit = 0.5 * Math.max(0, 1 - (v - top) / 0.055)
        if (lit > dither(x + 2, y + 1)) c = LIGHTER[c]
      }

      if (past(v, 0.95, 0.012, x, y)) c = GROUND

      out[y * w + x] = c
    }
  }
  return out
}
