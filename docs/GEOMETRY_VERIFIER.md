# Geometry Critic

`DeterministicGeometryVerifier` is the non-LLM authority for the repair loop. It
checks immutable box scenes in a common world frame and returns a structured
`DiagnosisReport`.

The current rules are:

- pairwise solid-box collision;
- floor penetration;
- contact and center-of-mass support margin;
- support-chain grounding to the floor or an anchored object.

Each failed diagnosis includes measurements, editable objects, locked objects,
the allowed repair tools and deterministic `MovePrescription` suggestions. The
verifier never trusts coordinates proposed by the LLM and never mutates the
input scene. Suggestions are retained for diagnosis/audit and replay; the LLM
receives semantic diagnosis data and the tool schemas, not a free-form delta API.

The configuration is versioned by a content fingerprint in
`configs/geometry-verifier-v1.json`. A repair tool computes a new immutable scene,
and the loop invokes the Critic again after every action. When a scene contains
functional affordances, `GeometryFunctionalCritic` runs this layer first and
then applies the optional Functional Critic after geometry passes.
