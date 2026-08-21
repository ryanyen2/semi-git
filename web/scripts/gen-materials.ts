// The printed sheets in docs/study/materials/, and the one source they come from.
//
// See HANDOUT_MD in src/study/content.ts for why there is only one source.
//
//   npm run gen:materials     write the files from src/study/content.ts
//   npm test                  fail if a file on disk has drifted
//
// Both commands run tests/schedule.test.ts, which writes when UPDATE_MATERIALS=1
// and compares otherwise. This file only names the pairs, so it needs no runner
// of its own: the repo has vitest, and it did not have the `vite-node` the old
// generator called, which meant regenerating a sheet was impossible and the test
// could only ever report that one had drifted.

import { HANDOUT_MD, sheetBriefMd, sheetTasksMd, sheetTutorialMd } from '../src/study/content'

// Paths are relative to web/, which is where npm runs vitest from.
export const SHEETS: Record<string, string> = {
  '../docs/study/materials/00-welcome.md': HANDOUT_MD,
  '../docs/study/materials/02-tutorial-git.md': sheetTutorialMd('git'),
  '../docs/study/materials/02-tutorial-sgt.md': sheetTutorialMd('sgt'),
  '../docs/study/materials/03-project-confplan.md': sheetBriefMd('confplan'),
  '../docs/study/materials/03-project-coursecraft.md': sheetBriefMd('coursecraft'),
  '../docs/study/materials/03-tasks-confplan.md': sheetTasksMd('confplan'),
  '../docs/study/materials/03-tasks-coursecraft.md': sheetTasksMd('coursecraft'),
}
