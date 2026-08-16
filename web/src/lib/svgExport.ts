// Export a rendered figure as a file that can go straight into a paper.
//
// The charts set every visual property as an SVG attribute rather than through
// a stylesheet, so serializing the element is enough: no computed-style walk, no
// rasterized layers, and text stays text, which is what makes it selectable in
// the PDF and re-typesettable if a reviewer asks for a bigger font.

function serialize(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  if (!clone.getAttribute('viewBox')) {
    const w = svg.getAttribute('width')
    const h = svg.getAttribute('height')
    if (w && h) clone.setAttribute('viewBox', `0 0 ${w} ${h}`)
  }
  // Strip anything that only exists for the interactive view.
  clone.querySelectorAll('[data-screen-only]').forEach((n) => n.remove())
  const body = new XMLSerializer().serializeToString(clone)
  return `<?xml version="1.0" encoding="UTF-8"?>\n${body}`
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

export function downloadSvg(svg: SVGSVGElement | null, filename: string) {
  if (!svg) return
  download(new Blob([serialize(svg)], { type: 'image/svg+xml;charset=utf-8' }), filename)
}

/** A raster copy at print resolution, for slides and for pasting into email. */
export async function downloadPng(svg: SVGSVGElement | null, filename: string, scale = 3) {
  if (!svg) return
  const text = serialize(svg)
  const w = Number(svg.getAttribute('width')) || svg.clientWidth
  const h = Number(svg.getAttribute('height')) || svg.clientHeight
  const img = new Image()
  const url = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml;charset=utf-8' }))
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve()
    img.onerror = () => reject(new Error('could not rasterize the figure'))
    img.src = url
  })
  const canvas = document.createElement('canvas')
  canvas.width = w * scale
  canvas.height = h * scale
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  URL.revokeObjectURL(url)
  await new Promise<void>((resolve) =>
    canvas.toBlob((b) => {
      if (b) download(b, filename)
      resolve()
    }, 'image/png'),
  )
}

export function downloadCsv(rows: Array<Record<string, unknown>>, filename: string) {
  if (rows.length === 0) return
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))]
  const esc = (v: unknown) => {
    if (v == null) return ''
    const s = String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const body = [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
  download(new Blob([body], { type: 'text/csv;charset=utf-8' }), filename)
}

export function downloadJson(data: unknown, filename: string) {
  download(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), filename)
}
