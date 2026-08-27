# ALEdb

ALEdb stores and analyses **adaptive laboratory evolution** experiments: the sequencing output
of evolved populations and clones, the mutations called from it, and the analyses that make
sense of them across a lineage.

It is a Django application that runs on its own or as part of an **assembled project** —
aledb-core plus whichever plugins a deployment wants. If you are reading this inside a
deployment's manual, everything under *Using ALEdb* is the platform, and the sections beside
it are what that deployment adds.

## What is in here

- **Using ALEdb** — getting an instance running, loading experiments, establishing a reference
  genome, and the commands that maintain it.
- **Extending ALEdb** — writing a plugin. aledb-core knows nothing about any plugin; a plugin
  registers itself at startup, and installing one is adding a git submodule.

## The shape of the data

Four levels, and every page here assumes them:

| | |
|---|---|
| **Project** | who owns the work. Access is granted here and nowhere else. |
| **Experiment** | one ALE study, with one reference genome. |
| **ALE / flask / isolate / replicate** | where a sample sits in the evolution — the `A-F-I-R` coordinate. |
| **Sample** | one sequencing run, carrying the mutations called from it. |

A mutation belongs to an experiment; a sample *observes* it. That distinction runs through
everything: deleting a mutation from one sample removes the observation and leaves the mutation
for the samples that still carry it.

## Getting started

[Quick start](using/quickstart.md) gets an instance running with no external services.
[Loading data](using/loading-data.md) is what to do next.
