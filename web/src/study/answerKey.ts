// What an answer key has to contain before it is worth uploading.
//
// Its own module because two places need the same answer: the upload in the
// experimenter console, and the test that runs it against the key actually
// checked into docs/study/. That test used to carry a hand-copied
// reimplementation of these rules, which meant it kept passing against its own
// copy while the shipped validator drifted -- a test that proves only that it
// agrees with itself.

import type { GroundTruth, RequestId } from '../lib/types'
import { PROJECTS } from '../lib/types'
import { BEHAVIOURS, REQUESTS, requestById } from './tasks'

/**
 * Why this is stricter than "is it a JSON object with the right top-level keys".
 *
 * Every failure it catches is silent at the point it matters. A key from an
 * earlier design of the task block uploads clean and scores nothing -- and the
 * "no answer key loaded" warning stays quiet precisely because a key IS loaded.
 * A key carrying only one project's answers leaves every participant on the
 * other project unscored. A reach answer naming behaviours the trial does not
 * offer lowers everybody's score by the same amount, so the ranking survives
 * and the breakage does not show.
 *
 * Throws on the first problem, with a message naming what is missing.
 */
export function validateGroundTruth(parsed: GroundTruth): void {
  if (!parsed.episodes || !parsed.requestKeys) {
    throw new Error('That file has no episodes or requestKeys in it.')
  }

  // Asked for by the live block rather than merely present in the file. A key
  // that answers only retired ids passes every per-entry check below, because
  // those checks only ever look at entries the key happens to carry.
  for (const spec of REQUESTS) {
    const entry = parsed.requestKeys[spec.id]
    if (spec.identify && !entry?.locate) {
      throw new Error(
        `That key has no locate answer for ${spec.id}, so the step asking which piece of ` +
          'work caused the defect would go unscored. It looks like a key from before the ' +
          'task block became locate-and-reverse.',
      )
    }
    if (spec.reach && !entry?.reach) {
      throw new Error(
        `That key has no reach answer for ${spec.id}, so the prediction would score every ` +
          'participant zero. Regenerate it with scripts/study/measure_reach_key.py.',
      )
    }
  }

  for (const [requestId, entry] of Object.entries(parsed.requestKeys)) {
    const spec = requestById(requestId as RequestId)

    // A reach answer naming an id the trial does not offer can never be ticked,
    // so it is a guaranteed miss that lowers everyone's score by the same amount
    // and leaves the ranking intact -- invisible in the results.
    if (spec?.reach && entry.reach) {
      if (entry.reach.length === 0) {
        throw new Error(`That key's reach answer for ${requestId} is empty.`)
      }
      const offered = BEHAVIOURS.map((b) => b.id)
      const unknown = entry.reach.filter((id) => !offered.includes(id))
      if (unknown.length > 0) {
        throw new Error(
          `That key answers ${requestId} with behaviours the trial does not offer: ` +
            `${unknown.join(', ')}. The key and the behaviour list have drifted apart.`,
        )
      }
      if (entry.reach.length === offered.length) {
        throw new Error(
          `That key says ${requestId} reaches all ${offered.length} behaviours, which ` +
            'ticking everything would score perfectly. It looks like a placeholder.',
        )
      }
    }

    if (!spec?.identify || !entry.locate) continue
    const projects = Object.keys(entry.locate)
    const absent = PROJECTS.filter((p) => !projects.includes(p))
    if (absent.length > 0) {
      throw new Error(
        `That key answers ${requestId} for ${projects.join(', ')} but not ${absent.join(', ')}. ` +
          'Participants on the missing project would go unscored.',
      )
    }
    for (const [project, accepted] of Object.entries(entry.locate)) {
      if (accepted.length === 0) {
        throw new Error(
          `That key accepts nothing for ${requestId} (${project}), so every answer is wrong.`,
        )
      }
    }
  }
}
