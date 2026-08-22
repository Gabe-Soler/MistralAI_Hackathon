import { MarkerIcon } from '@/components/ui/marker'
import { SITTING_CAT, catAspect, fitCat } from '@/lib/cats'

/**
 * The cat, in a box sized to its *visible* bounds.
 *
 * The GIF's frame is mostly transparent padding, so sizing the box to the frame
 * would leave a large empty area and knock any row it sits in out of alignment.
 */
export function CatFigure({ width, className }: { width: string; className?: string }) {
  return (
    <span
      className={className}
      style={{
        position: 'relative',
        display: 'block',
        width,
        aspectRatio: catAspect(SITTING_CAT),
      }}
    >
      <img src={SITTING_CAT.src} alt="" style={fitCat(SITTING_CAT)} />
    </span>
  )
}

/** The same cat, standing in for a spinner on a thinking marker. */
export function CatMarkerIcon({ width = '2.5rem' }: { width?: string }) {
  return (
    <MarkerIcon
      className="relative block shrink-0"
      style={{ width, height: 'auto', aspectRatio: catAspect(SITTING_CAT) }}
    >
      <img src={SITTING_CAT.src} alt="" style={fitCat(SITTING_CAT)} />
    </MarkerIcon>
  )
}
