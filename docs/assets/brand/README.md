<!-- SPDX-License-Identifier: MIT -->

# Ergon Lab visual system

This directory contains the draft raster identity system used by the Ergon Lab
portal.

## Art direction

The shared language is calm scientific editorial imagery: midnight navy,
porcelain, mist blue, steel, restrained cyan signals, tactile paper, glass,
metal, and mineral surfaces. The Ergon symbol may appear as architecture,
negative space, trajectory, fold, or engineering footprint rather than as a
repeated conventional logo placement.

## Provenance

The compositions were generated on 2026-08-31 with OpenAI's built-in image
generation capability using independently authored prompts and an Ergon symbol
reference supplied by the repository maintainer. No internal product image,
operator data, private topology, endpoint, source tree, or donor implementation
was used as an image input.

| File | Purpose | SHA-256 |
| --- | --- | --- |
| `ergon-lab-hero.jpg` | Global portal hero | `29b0b1f57e01565d65babb40de6c2e7118b2088d93e110b37db47a6eb70b2db1` |
| `discover-ergon.jpg` | Discovery cover | `d53d570eb697982de08624f0e079518ac63954bae0ca578b0cb28bf9e3d4874d` |
| `insights-and-vision.jpg` | Editorial cover | `908444db6fc4e233f2efd01aace33192272c0db98e859d6e155eff9ffb297b8e` |
| `network-observatory.jpg` | Observatory cover | `ca8cd581d69fdb875701555c04528ce60ab247ae6d886acef9eba8fccedfb006` |
| `node-engineering.jpg` | Node workshop cover | `850353dcd75d9fc913ed26aa2bec1267246129be4ab52a0a81dbe7c81a6bbc6f` |

## Editorial card system

The homepage uses small modular raster cards for Network Pulse, active research,
and the Lab's three public commitments. Image-generated art is kept separate
from deterministic typography so a reviewed value or status can change without
regenerating the visual language. Stable public paths make cards replaceable,
while new topics may add siblings.

The exact files, byte sizes, SHA-256 digests, prompt summaries, data boundary,
and source-sheet bindings are recorded in
[`cards/manifest.json`](cards/manifest.json). Network cards currently contain
publication-gate placeholders, not observed values.

## Publication approval

GitHub identity `ErgonSurfer` approved these five exact compositions and the
portal pages for publication under the MIT License on 2026-08-31. Inclusion of
the Ergon symbol does not by itself establish or transfer trademark rights.

The same identity explicitly instructed inclusion of the current eleven-card
system on 2026-08-31. Its exact license and provenance record is the card
manifest above.
