import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
// Stamps every host JSX element with data-ugh-loc="<file>:<line>", so a rendered
// region can be traced back to the symbol -- and so to the feature -- that produced
// it. It has to rewrite the source before the JSX transform, hence enforce: 'pre',
// which is why it is listed ahead of the react plugin.
// @ts-expect-error a plain .mjs plugin, deliberately untyped -- see the file's own notes
import ughLoc from './tools/vite-plugin-ugh-loc.mjs'

export default defineConfig({
  plugins: [ughLoc(), react()],
})
