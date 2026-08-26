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

import { HANDOUT_MD, sheetTasksMd, sheetTutorialMd } from '../src/study/content'

// Paths are relative to web/, which is where npm runs vitest from.
export const SHEETS: Record<string, string> = {
  '../docs/study/materials/00-welcome.md': HANDOUT_MD,
  // Four sheets rather than two, in both cases. The practice sheet quotes the
  // project's own files and the task sheet quotes that arm's own commands, so
  // "the git sheet" is not one document any more.
  '../docs/study/materials/02-tutorial-git-bikecount.md': sheetTutorialMd('git', 'bikecount'),
  '../docs/study/materials/02-tutorial-git-footfall.md': sheetTutorialMd('git', 'footfall'),
  '../docs/study/materials/02-tutorial-sgt-bikecount.md': sheetTutorialMd('sgt', 'bikecount'),
  '../docs/study/materials/02-tutorial-sgt-footfall.md': sheetTutorialMd('sgt', 'footfall'),
  '../docs/study/materials/03-tasks-bikecount-git.md': sheetTasksMd('bikecount', 'git'),
  '../docs/study/materials/03-tasks-bikecount-sgt.md': sheetTasksMd('bikecount', 'sgt'),
  '../docs/study/materials/03-tasks-footfall-git.md': sheetTasksMd('footfall', 'git'),
  '../docs/study/materials/03-tasks-footfall-sgt.md': sheetTasksMd('footfall', 'sgt'),
}
