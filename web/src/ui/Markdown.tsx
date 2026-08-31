import { useState } from 'react'
import type { ReactNode } from 'react'

/** A fenced code block with a copy button. Participants retype these commands
 * during timed stages; a mistyped flag measures typing, not the tool, so every
 * block offers its exact text in one click. Clipboard access can be denied in
 * odd webviews -- the button then just does nothing, and the text stays
 * selectable as before. */
function CodeBlock({ body }: { body: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <pre className="codeblock">
      <button
        type="button"
        className="code-copy"
        aria-label="Copy to clipboard"
        title="Copy to clipboard"
        onClick={() => {
          navigator.clipboard?.writeText(body).then(
            () => {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1500)
            },
            () => {},
          )
        }}
      >
        {copied ? '✓ copied' : '⧉ copy'}
      </button>
      <code>{body}</code>
    </pre>
  )
}

// A small block-level markdown renderer that returns React elements rather than
// HTML. The task text, the tutorials and the consent body are all markdown, and
// rendering them to elements instead of setting innerHTML means there is no
// injection surface to think about at all -- which matters because the consent
// body is editable from the dashboard.
//
// Supports what the study materials actually use: headings, paragraphs,
// blockquotes, fenced and indented code, bullet and numbered lists, tables,
// horizontal rules, and inline code, bold, italic and links.

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(!\[[^\]]*\]\([^)]+\))|(\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyBase}-${i++}`
    if (tok.startsWith('`')) out.push(<code key={key}>{tok.slice(1, -1)}</code>)
    else if (tok.startsWith('**')) out.push(<strong key={key}>{tok.slice(2, -2)}</strong>)
    else if (tok.startsWith('*')) out.push(<em key={key}>{tok.slice(1, -1)}</em>)
    else if (tok.startsWith('![')) {
      // Screenshots in the stage cards: `![what it shows](/stages/....png)`.
      // The alt text doubles as the caption for anyone who cannot see the image.
      const mm = /!\[([^\]]*)\]\(([^)]+)\)/.exec(tok)!
      out.push(<img key={key} className="md-img" src={mm[2]} alt={mm[1]} title={mm[1]} />)
    } else {
      const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(tok)!
      out.push(
        <a key={key} href={mm[2]} target="_blank" rel="noreferrer">
          {mm[1]}
        </a>,
      )
    }
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map((c) => c.trim())
}

/** One line of markdown, with no block wrapper around it. For list items and
 * table-like places where a <p> would break the layout. */
export function MarkdownLine({ children }: { children: string }) {
  return <>{inline(children ?? '', 'ml')}</>
}

export function Markdown({ children, className }: { children: string; className?: string }) {
  const lines = (children ?? '').replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let k = 0

  while (i < lines.length) {
    const line = lines[i]

    if (!line.trim()) {
      i++
      continue
    }

    // Fenced code
    if (/^\s*```/.test(line)) {
      const body: string[] = []
      i++
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++])
      i++
      blocks.push(<CodeBlock key={k++} body={body.join('\n')} />)
      continue
    }

    // Heading
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      const Tag = (['h1', 'h2', 'h3', 'h4'] as const)[h[1].length - 1]
      blocks.push(<Tag key={k++}>{inline(h[2], `h${k}`)}</Tag>)
      i++
      continue
    }

    // Rule
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push(<hr key={k++} />)
      i++
      continue
    }

    // Table
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      const head = splitRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitRow(lines[i]))
        i++
      }
      // Wrapped so a wide table scrolls inside itself. Unwrapped, its
      // min-content width propagates up through the page's grid and widens the
      // whole content column, which made every step carrying a table render
      // wider than every step that did not.
      blocks.push(
        <div className="scroll-x" key={k++}>
          <table>
            <thead>
              <tr>
                {head.map((c, ci) => (
                  <th key={ci}>{inline(c, `th${ci}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci}>{inline(c, `td${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    // Blockquote
    if (/^\s*>/.test(line)) {
      const body: string[] = []
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ''))
        i++
      }
      blocks.push(
        <blockquote key={k++}>
          <Markdown>{body.join('\n')}</Markdown>
        </blockquote>,
      )
      continue
    }

    // Lists
    const bullet = /^\s*[-*+]\s+/
    const numbered = /^\s*\d+[.)]\s+/
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line)
      const re = ordered ? numbered : bullet
      const items: string[] = []
      while (i < lines.length && re.test(lines[i])) {
        let text = lines[i].replace(re, '')
        i++
        // Continuation lines, indented under the marker.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !re.test(lines[i])) {
          text += ' ' + lines[i].trim()
          i++
        }
        items.push(text)
      }
      const List = ordered ? 'ol' : 'ul'
      blocks.push(
        <List key={k++}>
          {items.map((t, ii) => (
            <li key={ii}>{inline(t, `li${ii}`)}</li>
          ))}
        </List>,
      )
      continue
    }

    // Paragraph
    const para: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*(#{1,4}\s|>|```|[-*+]\s|\d+[.)]\s)/.test(lines[i]) &&
      !/^\s*(-{3,}|\*{3,})\s*$/.test(lines[i])
    ) {
      para.push(lines[i])
      i++
    }
    blocks.push(<p key={k++}>{inline(para.join(' '), `p${k}`)}</p>)
  }

  return <div className={className ? `markdown ${className}` : 'markdown'}>{blocks}</div>
}
