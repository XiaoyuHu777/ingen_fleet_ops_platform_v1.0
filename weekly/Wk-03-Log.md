# Week 3 Log

I used an open pretrained YOLOS-Tiny checkpoint to exercise the artifact path
for Rover. The ONNX export fixes the input size and records the preprocessing
details that affect the result. A robot needs the channel order, pixel
scaling, interpolation, normalization, and output interpretation to reproduce
the same input tensor and decode the graph. The checkpoint's standard
processor keeps the image aspect ratio, while this prototype stretches to a
fixed size. That choice makes the graph and interface simpler to reproduce.
It also creates an accuracy question that the Week 4 evaluation needs to
measure on representative Rover images.

The packaging work exposed a tension between repeatability and measurement.
A fresh latency run changes the report even on the same computer. Including
that reading directly in every build would change the signed digest. I made
measurement a separate step and tied its output to the ONNX graph, fixture
set, preprocessing contract, and ONNX Runtime version. The build repeats
the export and parity check using the recorded timing report. That gives a
reviewer a way to reproduce the package and a separate way to measure it
again on a different machine. The five synthetic fixtures are meant to
check numerical agreement across runtimes. They cannot establish whether
the detector handles outdoor scenes reliably.

The registry ties a version to one manifest digest and stores the signed
archive under that digest. PostgreSQL reserves the publication before the
registry writes to MinIO. If the object survives a failed database
transaction, a retry verifies and reuses it. This keeps retries from
overwriting a signed bundle. The lifecycle event commits with its state
change, and the database blocks edits and deletions of existing events.
That makes it possible to trace who moved an artifact forward and why,
which is the audit requirement that Sentinel Prime brought into the design.

I exercised the same bundle through publication, a repeat upload, and a
resumed download. Flipping one byte inside the ONNX file gives a specific
hash error in both the agent verifier and the publish API. The development
signer keeps its private key separate from the registry and verifier.
The next step is to measure task quality on labeled hard cases and have the
evaluation gate provide the evidence behind approval. The Week 3 sample
remains in the published state.
