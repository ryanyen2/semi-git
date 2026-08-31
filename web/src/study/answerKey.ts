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
    if ((spec.identify || spec.scoredLocate) && !entry?.locate) {
      throw new Error(
        `That key has no locate answer for ${spec.id}, so the stage asking which piece of ` +
          'work caused the defect would go unscored. It looks like a key from an earlier ' +
          'design of the task block.',
      )
    }
    // Every scored checklist needs a measured behaviour set, and every scored
    // multiple choice needs its correct value. A key missing either uploads
    // clean and scores nothing, which looks exactly like a study that did not
    // ask the question.
    for (const q of spec.quiz) {
      if (q.kind === 'behaviours' && q.scored && !entry?.reach) {
        throw new Error(
          `That key has no behaviour set for ${spec.id}, so its checklist would score every ` +
            'participant zero. Regenerate it with the measuring scripts in scripts/study/.',
        )
      }
      if (q.kind === 'choice' && q.scored) {
        const want = entry?.choices?.[q.id]
        if (!want) {
          throw new Error(
            `That key has no correct value for ${spec.id}'s "${q.id}" choice, so it would go ` +
              'unscored.',
          )
        }
        if (!q.options.some((o) => o.value === want)) {
          throw new Error(
            `That key answers ${spec.id}'s "${q.id}" choice with "${want}", which is not one ` +
              'of the options the stage offers. The key and the stage have drifted apart.',
          )
        }
      }
    }
  }

  for (const [requestId, entry] of Object.entries(parsed.requestKeys)) {
    const spec = requestById(requestId as RequestId)

    // A reach answer naming an id the trial does not offer can never be ticked,
    // so it is a guaranteed miss that lowers everyone's score by the same amount
    // and leaves the ranking intact -- invisible in the results.
    // The reach answer is per project now, because the two testbeds are harvested rather than
    // written and the same job genuinely lands on different pages in each. A plain array is still
    // read as "the same for both", so an older key still validates. Every project the study runs
    // has to be answered, or a participant on the unanswered one scores zero on a trial they
    // completed.
    const wantsBehaviours = spec?.quiz.some((q) => q.kind === 'behaviours' && q.scored) ?? false
    if (wantsBehaviours && entry.reach) {
      const perProject: Array<[string, string[]]> = Array.isArray(entry.reach)
        ? PROJECTS.map((p) => [p, entry.reach as string[]])
        : Object.entries(entry.reach as Record<string, string[]>)
      const answered = perProject.map(([p]) => p)
      const missing = PROJECTS.filter((p) => !answered.includes(p))
      if (missing.length > 0) {
        throw new Error(
          `That key's reach answer for ${requestId} covers ${answered.join(', ') || 'nothing'} ` +
            `but not ${missing.join(', ')}. Participants on the missing project would score ` +
            'zero on a prediction they actually made.',
        )
      }
      const offered = BEHAVIOURS.map((b) => b.id)
      for (const [project, ids] of perProject) {
        if (ids.length === 0) {
          throw new Error(`That key's reach answer for ${requestId} on ${project} is empty.`)
        }
        const unknown = ids.filter((id) => !offered.includes(id))
        if (unknown.length > 0) {
          throw new Error(
            `That key answers ${requestId} on ${project} with behaviours the trial does not ` +
              `offer: ${unknown.join(', ')}. The key and the behaviour list have drifted apart.`,
          )
        }
        if (ids.length === offered.length) {
          throw new Error(
            `That key says ${requestId} on ${project} reaches all ${offered.length} behaviours, ` +
              'which ticking everything would score perfectly. It looks like a placeholder.',
          )
        }
      }
    }

    if (!(spec?.identify || spec?.scoredLocate) || !entry.locate) continue
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
