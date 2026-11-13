"""Pin detection matching and the training-independent preprocessing contract."""

import json
from pathlib import Path

import numpy as np
import pytest

from agent.artifacts import InputContract
from pipeline.detection_metrics import Detection, errors, mean_average_precision
from pipeline.evaluate import preprocess


def test_ap_ranks_false_positives_and_matches_each_object_once() -> None:
    truth = Detection(1, (0, 0, 10, 10))
    labels = {"positive": [truth], "empty": []}
    duplicate = Detection(1, truth.box, 0.75)
    false = Detection(1, (0, 0, 2, 2), 0.99)
    predictions = {"positive": [truth, duplicate], "empty": [false]}
    assert mean_average_precision(labels, predictions) == (1.0, 1.0)
    predictions = {
        "positive": [Detection(1, truth.box, 0.9), duplicate],
        "empty": [false],
    }
    assert mean_average_precision(labels, predictions) == (0.5, 0.5)
    assert errors([truth], [truth, duplicate], 0.5) == (0, 1)
    assert errors([truth], [Detection(2, truth.box)], 0.5) == (1, 1)
    assert mean_average_precision({"empty": []}, {"empty": [false]}) == (None, None)


def test_manifest_contract_detects_rgb_bgr_swap() -> None:
    fixture = json.loads(Path("pipeline/yolos-tiny.json").read_bytes())["inputs"]
    contract = InputContract.model_validate_json(json.dumps(fixture))
    rgb = np.array([[[255, 127, 0]]], dtype=np.uint8)
    out = preprocess(rgb, contract)
    bgr = preprocess(rgb[:, :, ::-1], contract)
    assert out.shape == contract.shape
    assert out.dtype == np.float32
    expected = (rgb[0, 0] / 255 - np.array(contract.mean)) / np.array(contract.std)
    np.testing.assert_allclose(out[0, :, 0, 0], expected, atol=1e-6)
    assert np.max(np.abs(out - bgr)) > 4.0
    fixture["color_order"] = "BGR"
    with pytest.raises(ValueError, match="color_order"):
        InputContract.model_validate_json(json.dumps(fixture))


def test_half_pixel_resize_clamps_edges_and_interpolates() -> None:
    fixture = json.loads(Path("pipeline/yolos-tiny.json").read_bytes())["inputs"]
    fixture.update(shape=[1, 3, 1, 4], mean=[0.0] * 3, std=[1.0] * 3)
    contract = InputContract.model_validate_json(json.dumps(fixture))
    pixels = np.array([[[0, 0, 0], [255, 255, 255]]], dtype=np.uint8)
    np.testing.assert_allclose(
        preprocess(pixels, contract)[0, 0, 0], [0, 0.25, 0.75, 1]
    )
