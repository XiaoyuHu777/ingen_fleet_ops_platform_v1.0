# Golden detection dataset

Version `coco2017-val-ingen-w04.2` contains **140 evaluation images** and
**547 bounding boxes**. It uses 64 distinct COCO 2017 validation photographs,
60 derived frames and 16 procedural negative frames. Each original contributes
to one scene group. The 20 outdoor scenes are paired across nominal, low light,
motion blur and sensor noise so changes in those slices can be compared on the
same source scenes.

**Dataset SHA-256** `f75b1e7ad2afd597b90530b236caee30d8192bd325634e30421513e2b127cadc`

The digest is the SHA-256 of the exact committed [labels.json](labels.json)
bytes. The label manifest pins every image by SHA-256 as well as the source
JPEG digest for every photograph. Any change to the image bytes, annotation
coordinates, slice assignments or provenance breaks the chain of verification.

| Slice | Images | Boxes | Source and annotations |
| --- | ---: | ---: | --- |
| Nominal | 20 | 61 | Unchanged COCO validation JPEGs of outdoor scenes, including vehicles and animals. |
| Low light | 20 | 61 | Same 20 scenes. Multiply every 8-bit RGB sample by 0.22, round to nearest even and clip to the uint8 interval. Bounding boxes stay fixed. |
| Motion blur | 20 | 61 | Same 20 scenes. Apply a centered 13-tap uniform horizontal kernel (13 weights of 1/13) independently to each channel with edge replication, then round and clip. Bounding boxes stay fixed. |
| Sensor noise | 20 | 61 | Same 20 scenes. Add independently sampled Gaussian noise with mean zero and standard deviation 18 DN per pixel and RGB channel using NumPy PCG64 and the per-frame seed. Round and clip. Boxes stay fixed. |
| Small or thin | 20 | 176 | Separate COCO originals containing at least one selected object whose box occupies 0.08–3.5% of frame area and has short-to-long side ratio at most 0.35. Includes larger co-occurring boxes for matching. |
| Empty scene | 16 | 0 | Deterministic procedural 384 × 288 camera backgrounds with lighting gradients, sinusoidal structure and low-amplitude noise. An empty box array explicitly represents zero COCO targets. |
| Fari indoor close range | 24 | 127 | Separate COCO originals reviewed for indoor context and a foreground target of at least 16% of the frame area. Household scenes include tables, beds, kitchen items and laptops. Area is a proxy for close range. |

## Provenance and licenses

- The photographs are drawn from [COCO 2017 validation](https://cocodataset.org/#download).
  The source archive is [instances annotations](https://s3.amazonaws.com/images.cocodataset.org/annotations/annotations_trainval2017.zip).
  The COCO Consortium publishes its annotations under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Each row records the COCO image and annotation IDs, the original Flickr
  page, credited creator, title, source JPEG checksum and image license.
- The selected photos use
  [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) or
  [CC BY-SA 2.0](https://creativecommons.org/licenses/by-sa/2.0/) as displayed on the original
  Flickr pages checked on November 13, 2026. The selection excludes photographs
  whose open license or attribution could not be confirmed there. Each
  transformed photo and overlay retains the indicated source license, with
  modifications identified.
- The generated empty backgrounds and their empty labels are dedicated to the
  public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
- COCO annotations with crowds or the person category were excluded at source
  selection. Selected images were visually screened for scene suitability and
  identifiable people. Boxes retain the original IDs and category IDs and are
  clipped from COCO XYWH to zero-origin, half-open XYXY pixel bounds. Each
  positive slice uses all usable annotations on its images. Derivations keep
  original dimensions and store exact RGB bytes as PPM.

## Reproduce and verify

From the project root, using the locked packaging dependencies:

```sh
uv sync --locked --group packaging
python -m eval.golden.fetch
(cd eval/golden && shasum -a 256 -c DATASET.sha256)
```

Use `--source-cache PATH` to reuse verified original JPEGs. The fetcher checks
the source and resulting frame hashes before writing media. The evaluator
independently checks the dataset digest, every image digest and each frame's
dimensions before inference. Images are materialized under `images/` through
the checksummed downloader, keeping the Git repository small.

The paired outdoor images are correlated. The 16 procedural negatives check
false positives on controlled backgrounds. Natural target-free scenes would
measure false positives on richer textures.
The Fari slice tests household scene coverage on public photographs. Its
quality numbers do not establish performance on Fari's actual cameras or
clinical tasks. Use these thresholds for prototype regression control.
