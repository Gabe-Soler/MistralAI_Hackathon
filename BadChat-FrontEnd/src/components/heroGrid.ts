/**
 * Geometry for Bottom_hero_and_top_next_page.svg.
 *
 * The artwork sits on a 16 × 28 grid (columns of 100 units, rows of 512/28)
 * and is mirror-symmetric both left-to-right and top-to-bottom. That vertical
 * symmetry is what lets it split at the fold into a descending staircase that
 * closes the hero and its rising reflection that opens the next section.
 */
export const COLS = 16
export const ROWS = 28
export const COL = 100
export const ROW = 512 / 28
export const FOLD = ROWS / 2

/** Each tier doubles in size and starts further down: spans 1, 2, 4. */
export const TIERS = [0, 1, 2].map((t) => ({
  span: 1 << t,
  start: 2 * ((1 << t) - 1),
}))

/** Distinct animated colours: one per (tier, depth) pair. */
export const SLOTS = TIERS.length * 2

/** Spread of the ramp across the artwork, in palette cycles. */
const TIER_STAGGER = 0.16
const DEPTH_STAGGER = 0.08

/** Lower tiers lead the ramp, so colour breaks upward through the staircase. */
export const offsetFor = (slot: number) =>
  Math.floor(slot / 2) * TIER_STAGGER + (slot % 2) * DEPTH_STAGGER

export interface Cell {
  col: number
  row: number
  span: number
  accent: boolean
  slot: number
}

/** The descending staircase — rows 0…13, which is the top half. */
export const TOP_CELLS: Cell[] = TIERS.flatMap((tier, tierIndex) =>
  [0, 1].flatMap((row) =>
    [0, 1].flatMap((depth) => {
      // Rows alternate, so each tier reads as a two-row dither.
      const accent = row === 0 ? depth === 1 : depth === 0
      const shared = {
        row: tier.start + row * tier.span,
        span: tier.span,
        accent,
        slot: tierIndex * 2 + depth,
      }
      return [
        { ...shared, col: depth * tier.span },
        { ...shared, col: COLS - (depth + 1) * tier.span },
      ]
    }),
  ),
)

/** The rising reflection — the same cells flipped about the fold. */
export const BOTTOM_CELLS: Cell[] = TOP_CELLS.map((cell) => ({
  ...cell,
  row: ROWS - cell.row - cell.span - FOLD,
}))

/**
 * Top surface of the first step of the right-hand staircase (the row-6 block),
 * as a fraction of the half's height measured up from its bottom edge — so
 * anything standing on it can be placed in fractions and scale with the artwork.
 */
export const FIRST_STEP_SURFACE = (FOLD - TIERS[2].start) / FOLD

/** The half's height as a fraction of its width. */
export const HALF_ASPECT = (FOLD * ROW) / (COLS * COL)
