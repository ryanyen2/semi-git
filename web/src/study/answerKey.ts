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
import { BEHAVIOURS, requestById } from './tasks'

/**
 * Why this is stricter than "is it a JSON object with the right top-level keys".
 *
 * Every failure it catches is silent at the point it matters. A key from before
 * request one became closed questions has `episodes` and `requestKeys` and no
 * `choices`, so it uploads clean and scores nothing -- and the "no answer key
 * loaded" warning stays quiet precisely because a key IS loaded. A key whose
 * question ids no longer match the ones being asked scores every participant
 * zero out of three, which is indistinguishable from everybody getting it wrong.
 * A key carrying only one project's answers leaves every participant on the
 * other project unscored.
 *
 * Throws on the first problem, with a message naming what is missing.
 */
export function validateGroundTruth(parsed: GroundTruth): void {
  if (!parsed.episodes || !parsed.requestKeys) {
    throw new Error('That file has no episodes or requestKeys in it.')
  }

  if (Object.values(parsed.requestKeys).filter((k) => k.choices).length === 0) {
    throw new Error(
      'That key has no closed-question answers in it, so request one would go unscored. ' +
        'It looks like a key from before request one became multiple choice.',
    )
  }

  for (const [requestId, entry] of Object.entries(parsed.requestKeys)) {
    const spec = requestById(requestId as RequestId)

    // A reach trial with no key scores by set overlap against nothing, so every
    // participant gets zero and the run looks like a hard trial rather than a
    // missing answer. An id the trial does not offer is worse than absent: it can
    // never be ticked, so it is a guaranteed miss that lowers everyone's score by
    // the same amount and leaves the ranking intact.
    if (spec?.reach) {
      if (!entry.reach || entry.reach.length === 0) {
        throw new Error(
          `That key has no reach answer for ${requestId}, so the prediction trial ` +
            'would score every participant zero. Regenerate it with ' +
            'scripts/study/measure_reach_key.py.',
        )
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

    const asked = spec?.choices ?? []
    if (asked.length === 0) continue
    if (!entry.choices) {
      throw new Error(`That key has no closed-question answers for ${requestId}.`)
    }
    const projects = Object.keys(entry.choices)
    const absent = PROJECTS.filter((p) => !projects.includes(p))
    if (absent.length > 0) {
      throw new Error(
        `That key answers ${requestId} for ${projects.join(', ')} but not ${absent.join(', ')}. ` +
          'Participants on the missing project would go unscored.',
      )
    }
    for (const [project, answers] of Object.entries(entry.choices)) {
      const missing = asked.map((q) => q.id).filter((id) => !(id in answers))
      if (missing.length > 0) {
        throw new Error(
          `That key is missing answers for ${requestId} (${project}): ${missing.join(', ')}. ` +
            'Every question asked needs one, or they score zero.',
        )
      }
    }
  }
}
