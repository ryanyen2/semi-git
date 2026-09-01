// The files stage 1's map names, for one project, one per line.
//
//   node scripts/study/stage1-map-files.mjs <bikecount|footfall>
//
// The map is stage 1: the participant reads it beside their setup's view of the
// history and matches the two up. A path in it that the repository does not
// contain is unfalsifiable from their side -- it looks exactly like a file they
// cannot find -- so the bundle build checks every one against the repo it ships
// (see the rehearsal gate in scripts/make-study-bundle.sh).
//
// It reads `web/src/study/tasks.ts` as text rather than importing it, because
// that module is TypeScript and this has to run from a bash gate with nothing
// but node. Finding nothing is an error, not an empty answer: a regex that
// silently stops matching would turn this check off without saying so.
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const project = process.argv[2]
if (project !== 'bikecount' && project !== 'footfall') {
  console.error('usage: node stage1-map-files.mjs <bikecount|footfall>')
  process.exit(2)
}

const root = join(dirname(fileURLToPath(import.meta.url)), '../..')
const src = readFileSync(join(root, 'web/src/study/tasks.ts'), 'utf8')

const words = src.indexOf('export const PROJECT_WORDS')
if (words < 0) throw new Error('no PROJECT_WORDS in web/src/study/tasks.ts')
const start = src.indexOf(`\n  ${project}: {`, words)
if (start < 0) throw new Error(`no ${project} entry in PROJECT_WORDS`)
const storyAt = src.indexOf('story: [', start)
const end = src.indexOf('\n    ],', storyAt)
if (storyAt < 0 || end < 0) throw new Error(`no story array for ${project}`)
const story = src.slice(storyAt, end)

// Only the `code` column. The other two columns quote page names and numbers,
// and `pages/` in a sentence about the dashboard is not a file to check.
const files = new Set()
for (const line of story.split('\n')) {
  const code = /^\s*code: (['"])(.*)\1,\s*$/.exec(line)
  if (!code) continue
  for (const [, path] of code[2].matchAll(/`([^`]+)`/g)) {
    // "every page under `pages/`" names a directory, not a file.
    if (path.endsWith('/')) continue
    files.add(path)
  }
}
if (files.size === 0) throw new Error(`found no files in ${project}'s story -- the format moved`)
for (const f of [...files].sort()) console.log(f)
