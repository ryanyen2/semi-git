# Literature Deepening: Six Additions

Each entry identifies a place where the paper says something true but says it flat,
and proposes an exact addition that connects it to a deeper principle the reader
already half-knows. None of the six papers below is currently cited.


## 1. Parnas 1972 --- Section 3, "What the five have in common"

**File:** `sections/03-scenarios.tex`, around line 138--144

**Existing sentence it would follow:**

> Every one of these operations takes a set of edits as its argument, and the
> developer identifies that set by the purpose the edits served.

**Proposed addition:**

```latex
Parnas argued that the criterion for decomposing a system into modules is the
design decision each module hides~\cite{parnas1972}. The set a developer
identifies by purpose is exactly this: the functions that would all have to
change if that design decision were revised. The developer's natural unit of
versioning is the information-hiding module, and the five operations above are
five ways of asking a version control system to address one.
```

**Why this deepens rather than decorates:** The paper asserts that the developer
identifies sets by purpose but does not name WHY purpose is the right grouping
criterion. Parnas supplies the reason every reader in SE already knows: things
that hide the same decision change together. This makes the reader go "of course
the version control unit should be the module" rather than "that sounds reasonable."


## 2. Suchman 1987 --- Section 7b, over-decomposition finding

**File:** `sections/07b-discussion.tex`, within the paragraph on inferred names
(around the passage that says "features are not recovered intents... they are
recovered sub-intents," approximately line 258--265)

**Existing sentence it would follow:**

> The finding kills a framing the paper once used: features are not recovered
> intents. At this granularity they are recovered sub-intents---structural
> communities that correspond to what a developer did at the code level, not to
> what they asked for at the request level.

**Proposed addition:**

```latex
Suchman's distinction between plans and situated actions explains why no
refinement of the clustering will close this gap~\cite{suchman1987}. A
developer's request is a plan in her sense: a resource that oriented and
constrained the agent's work, not a specification that determined it. The four
to six structural communities the tool recovers are the situated actions the plan
gave rise to, and they outnumber the plan because one orienting statement
produces several independent implementation decisions the statement never
mentioned. The gap between request and feature is the gap between planning and
doing, and it is structural rather than an artefact of weak inference.
```

**Why this deepens rather than decorates:** The paper calls the 4--6x ratio a
finding about the tool's granularity. Suchman reframes it as a finding about the
nature of plans: a plan cannot determine its own execution, so one plan always
produces multiple actions. This tells the reader that the ratio will not improve
with better algorithms---it is the measured distance between two levels of
description.


## 3. Beaudouin-Lafon 2000 --- Section 4 design introduction

**File:** `sections/04-design.tex`, around lines 11--16

**Existing sentence it would follow:**

> A version control system's unit of record decides which questions a developer
> can ask of their own history. The unit should match the grain a developer thinks
> at---because those are the questions they will actually ask.

**Proposed addition:**

```latex
Beaudouin-Lafon called this move \emph{reification}: turning a concept that
exists only in a user's description of their work into a first-class object the
system can display and the user can
manipulate~\cite{beaudouinlafon2000}. The decomposition of code into named
features was formerly a concept a developer held and a commit message
approximated. Making it into an object---one a developer can point at, revert,
restore, rename, or hand to a colleague---is what makes the five operations of
Section~\ref{sec:scenarios} expressible as commands rather than as labour.
```

**Why this deepens rather than decorates:** The paper states that the unit should
match the grain a developer thinks at, but does not name the design principle that
justifies the claim. Beaudouin-Lafon's reification gives the reader a word for
what sgt does: it turns a tacit concept into a manipulable object. This is the
specific mechanism by which a finer record enables new operations, and naming it
makes the design logic visible.


## 4. Simon 1962 --- Section 4, inferred names and clustering

**File:** `sections/04-design.tex`, around lines 218--220 (the clustering
paragraph in Section 4.4)

**Existing sentence it would follow:**

> Functions that changed in the same saves and that refer to one another are
> grouped together, using the change coupling signal established by Zimmermann et
> al.\ and Ying et al.~\cite{zimmermann2004,ying2004} and a community detection
> method over the resulting graph~\cite{traag2019}.

**Proposed addition:**

```latex
The theoretical ground for this is Simon's near-decomposability
thesis~\cite{simon1962}: a complex system's interactions are dense within
subsystems and sparse between them, and any algorithm that measures interaction
density will find the subsystem boundaries. Community detection exploits this
structure. The failure mode the next paragraph reports---a hub function that
couples everything---is exactly the point where near-decomposability breaks down:
a node with edges into every community sits at no boundary, so it is placed
arbitrarily and drags unrelated work into whichever group it lands in.
```

**Why this deepens rather than decorates:** The paper states the mechanism
(co-change + community detection) but not why anyone should believe this
mechanism finds real features. Simon supplies the reason: features ARE the
near-decomposable subsystems, and community detection finds them precisely because
within-feature coupling exceeds between-feature coupling. Naming this also makes
the hub-function failure feel principled rather than incidental---it is the known
exception to near-decomposability.


## 5. Star & Griesemer 1989 --- Section 7, "What a colleague sees"

**File:** `sections/07-discussion.tex`, around lines 342--349

**Existing sentence it would follow:**

> Because the records are files in the repository, everyone who pulls the
> repository gets them whether they asked for them or not, and this section works
> through what that means for three people who are not the developer running
> \sgt{}: a colleague who has never installed it, a colleague who installed a
> different version of it, and the reviewer of a pull request.

**Proposed addition:**

```latex
Star and Griesemer called an artefact that inhabits several social worlds and
satisfies each world's requirements without being identical in any of them a
\emph{boundary object}~\cite{star1989}. The \sgt{} record is one: the developer
who runs it sees named features and revertible sets; the colleague who has never
installed it sees ordinary committed files they can ignore; the reviewer sees a
diff whose code portion they approve while its metadata passes unread. Each
reading is coherent on its own terms, which is what lets the record exist in a
repository where not everyone has agreed to use the tool that writes it.
```

**Why this deepens rather than decorates:** The section already describes how
three people read the same artifact differently. "Boundary object" gives the
reader the concept that explains why partial adoption works at all: the artifact
does not demand consensus about what it IS, only coexistence within the same
repository. This helps a reader reasoning about team adoption see why sgt does
not require everyone to install it.


## 6. Winograd & Flores 1986 --- Introduction

**File:** `sections/01-intro.tex`, around lines 11--18

**Existing sentence it would follow:**

> The condition that made the agreement sound---that the developer holds a model
> of what changed and why---no longer holds reliably.

**Proposed addition:**

```latex
Winograd and Flores called this kind of event a \emph{breakdown}: an assumption
so thoroughly embedded in practice that it was invisible as an assumption, until
the practice changed and the assumption became the thing that needs
redesigning~\cite{winograd1986}. The assumption that the developer holds the
decomposition was never stated as a design decision---it was the ground on which
every other design decision rested---and it became visible only when a developer
who typed three sentences found they could not supply it.
```

**Why this deepens rather than decorates:** The paper says the condition "no
longer holds reliably" but does not characterise what kind of event this is. The
reader who recognises a breakdown understands that the paper is not proposing a
feature (a nicer revert) but responding to a structural shift in the design
situation. This frames the entire contribution correctly: the problem is not that
git lacks a command, but that an assumption git was built on has collapsed.


---

## BibTeX entries needed

```bibtex
@article{parnas1972,
  author    = {David L. Parnas},
  title     = {On the Criteria To Be Used in Decomposing Systems into Modules},
  journal   = {Communications of the ACM},
  volume    = {15},
  number    = {12},
  pages     = {1053--1058},
  year      = {1972},
}

@book{suchman1987,
  author    = {Lucy A. Suchman},
  title     = {Plans and Situated Actions: The Problem of Human-Machine Communication},
  publisher = {Cambridge University Press},
  year      = {1987},
}

@inproceedings{beaudouinlafon2000,
  author    = {Michel Beaudouin-Lafon},
  title     = {Instrumental Interaction: An Interaction Model for Designing Post-{WIMP} User Interfaces},
  booktitle = {Proceedings of the SIGCHI Conference on Human Factors in Computing Systems (CHI)},
  pages     = {446--453},
  year      = {2000},
  publisher = {ACM},
}

@article{simon1962,
  author    = {Herbert A. Simon},
  title     = {The Architecture of Complexity},
  journal   = {Proceedings of the American Philosophical Society},
  volume    = {106},
  number    = {6},
  pages     = {467--482},
  year      = {1962},
}

@article{star1989,
  author    = {Susan Leigh Star and James R. Griesemer},
  title     = {Institutional Ecology, `Translations' and Boundary Objects: Amateurs and Professionals in {Berkeley's} Museum of Vertebrate Zoology, 1907--39},
  journal   = {Social Studies of Science},
  volume    = {19},
  number    = {3},
  pages     = {387--420},
  year      = {1989},
}

@book{winograd1986,
  author    = {Terry Winograd and Fernando Flores},
  title     = {Understanding Computers and Cognition: A New Foundation for Design},
  publisher = {Ablex Publishing},
  year      = {1986},
}
```
