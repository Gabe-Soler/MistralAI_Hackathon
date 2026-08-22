import type { CSSProperties } from 'react'

export interface CatAsset {
  src: string
  /** Frame size in px. */
  frame: { w: number; h: number }
  /**
   * The opaque box inside that frame. Measured as the union across every
   * animation frame — the sitting cat's tail swings well past where it sits on
   * frame 1, so a single-frame measurement would place the cat wrongly.
   */
  content: { x: number; y: number; w: number; h: number }
}

export const SITTING_CAT: CatAsset = {
  src: '/animated-sitting-cat.gif',
  frame: { w: 896, h: 896 },
  content: { x: 32, y: 288, w: 704, h: 320 },
}

export const WALKING_CAT: CatAsset = {
  src: '/cat-walking-white.gif',
  frame: { w: 800, h: 600 },
  content: { x: 70, y: 60, w: 627, h: 480 },
}

/** Aspect ratio of the cat itself, for sizing the box it stands in. */
export const catAspect = ({ content }: CatAsset) => `${content.w} / ${content.h}`

/**
 * Both GIFs are mostly transparent padding, so positioning the raw <img> leaves
 * the cat floating somewhere inside an invisible box. Given a wrapper sized to
 * the cat's *visible* box (use `catAspect` for its ratio), this scales and
 * offsets the frame so the cat lands exactly on that wrapper's edges.
 */
export function fitCat({ frame, content }: CatAsset): CSSProperties {
  return {
    position: 'absolute',
    width: `${(frame.w / content.w) * 100}%`,
    height: `${(frame.h / content.h) * 100}%`,
    left: `${(-content.x / content.w) * 100}%`,
    top: `${(-content.y / content.h) * 100}%`,
    maxWidth: 'none',
    imageRendering: 'pixelated',
  }
}

/**
 * The cursor. Not animated — it's a single sprite, pointed by the hand that
 * moves it (see CatCursor).
 */
export const EVIL_CAT: CatAsset = {
  src: '/evil-cat.png',
  frame: { w: 300, h: 300 },
  content: { x: 84, y: 75, w: 122, h: 141 },
}
