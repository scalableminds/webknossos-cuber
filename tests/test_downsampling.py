import logging
from typing import Tuple

import numpy as np

from wkcuber.api.Dataset import WKDataset
from wkcuber.downsampling_utils import (
    InterpolationModes,
    downsample_cube,
    downsample_cube_job,
    get_next_mag,
)
import wkw
from wkcuber.mag import Mag
from wkcuber.utils import WkwDatasetInfo, open_wkw
from wkcuber.downsampling_utils import _mode, non_linear_filter_3d
import shutil

WKW_CUBE_SIZE = 1024
CUBE_EDGE_LEN = 256

source_info = WkwDatasetInfo("testdata/WT1_wkw", "color", 1, wkw.Header(np.uint8))
target_info = WkwDatasetInfo("testoutput/WT1_wkw", "color", 2, wkw.Header(np.uint8))


def read_wkw(
    wkw_info: WkwDatasetInfo, offset: Tuple[int, int, int], size: Tuple[int, int, int]
):
    with open_wkw(wkw_info) as wkw_dataset:
        return wkw_dataset.read(offset, size)


def test_downsample_cube():
    buffer = np.zeros((CUBE_EDGE_LEN,) * 3, dtype=np.uint8)
    buffer[:, :, :] = np.arange(0, CUBE_EDGE_LEN)

    output = downsample_cube(buffer, (2, 2, 2), InterpolationModes.MODE)

    assert output.shape == (CUBE_EDGE_LEN // 2,) * 3
    assert buffer[0, 0, 0] == 0
    assert buffer[0, 0, 1] == 1
    assert np.all(output[:, :, :] == np.arange(0, CUBE_EDGE_LEN, 2))


def test_downsample_mode():

    a = np.array([[1, 3, 4, 2, 2, 7], [5, 2, 2, 1, 4, 1], [3, 3, 2, 2, 1, 1]])

    result = _mode(a)
    expected_result = np.array([1, 3, 2, 2, 1, 1])

    assert np.all(result == expected_result)


def test_downsample_median():

    a = np.array([[1, 3, 4, 2, 2, 7], [5, 2, 2, 1, 4, 1], [3, 3, 2, 2, 1, 1]])

    result = np.median(a, axis=0)
    expected_result = np.array([3, 3, 2, 2, 2, 1])

    assert np.all(result == expected_result)


def test_non_linear_filter_reshape():
    a = np.array([[[1, 3], [1, 4]], [[4, 2], [3, 1]]], dtype=np.uint8)

    a_filtered = non_linear_filter_3d(a, [2, 2, 2], _mode)
    assert a_filtered.dtype == np.uint8
    expected_result = [1]
    assert np.all(expected_result == a_filtered)

    a = np.array([[[1, 3], [1, 4]], [[4, 3], [3, 1]]], np.uint32)

    a_filtered = non_linear_filter_3d(a, [2, 2, 1], _mode)
    assert a_filtered.dtype == np.uint32
    expected_result = [1, 3]
    assert np.all(expected_result == a_filtered)


def downsample_test_helper(use_compress):
    try:
        shutil.rmtree(target_info.dataset_path)
    except:
        pass

    offset = (1, 2, 0)
    source_buffer = read_wkw(
        source_info,
        tuple(a * WKW_CUBE_SIZE * 2 for a in offset),
        (CUBE_EDGE_LEN * 2,) * 3,
    )[0]
    assert np.any(source_buffer != 0)

    downsample_args = (
        source_info,
        target_info,
        (2, 2, 2),
        InterpolationModes.MAX,
        offset,
        CUBE_EDGE_LEN,
        use_compress,
        True,
    )
    downsample_cube_job(downsample_args)

    assert np.any(source_buffer != 0)
    block_type = (
        wkw.Header.BLOCK_TYPE_LZ4HC if use_compress else wkw.Header.BLOCK_TYPE_RAW
    )
    target_info.header.block_type = block_type

    target_buffer = read_wkw(
        target_info, tuple(a * WKW_CUBE_SIZE for a in offset), (CUBE_EDGE_LEN,) * 3
    )[0]
    assert np.any(target_buffer != 0)

    assert np.all(
        target_buffer
        == downsample_cube(source_buffer, [2, 2, 2], InterpolationModes.MAX)
    )


def test_downsample_cube_job():
    downsample_test_helper(False)


def test_compressed_downsample_cube_job():
    downsample_test_helper(True)


def test_downsample_multi_channel():
    offset = (0, 0, 0)
    num_channels = 3
    size = (32, 32, 10)
    source_data = (
        128 * np.random.randn(num_channels, size[0], size[1], size[2])
    ).astype("uint8")
    file_len = 32

    source_info = WkwDatasetInfo(
        "testoutput/multi-channel-test",
        "color",
        1,
        wkw.Header(np.uint8, num_channels, file_len=file_len),
    )
    target_info = WkwDatasetInfo(
        "testoutput/multi-channel-test",
        "color",
        2,
        wkw.Header(np.uint8, file_len=file_len),
    )
    try:
        shutil.rmtree(source_info.dataset_path)
        shutil.rmtree(target_info.dataset_path)
    except:
        pass

    with open_wkw(source_info) as wkw_dataset:
        print("writing source_data shape", source_data.shape)
        wkw_dataset.write(offset, source_data)
    assert np.any(source_data != 0)

    downsample_args = (
        source_info,
        target_info,
        (2, 2, 2),
        InterpolationModes.MAX,
        tuple(a * WKW_CUBE_SIZE for a in offset),
        CUBE_EDGE_LEN,
        False,
        True,
    )
    downsample_cube_job(downsample_args)

    channels = []
    for channel_index in range(num_channels):
        channels.append(
            downsample_cube(
                source_data[channel_index], [2, 2, 2], InterpolationModes.MAX
            )
        )
    joined_buffer = np.stack(channels)

    target_buffer = read_wkw(
        target_info,
        tuple(a * WKW_CUBE_SIZE for a in offset),
        list(map(lambda x: x // 2, size)),
    )
    assert np.any(target_buffer != 0)

    assert np.all(target_buffer == joined_buffer)


def test_anisotropic_mag_calculation():
    mag_tests = [
        [(10.5, 10.5, 24), Mag(1), Mag((2, 2, 1))],
        [(10.5, 10.5, 21), Mag(1), Mag((2, 2, 1))],
        [(10.5, 24, 10.5), Mag(1), Mag((2, 1, 2))],
        [(24, 10.5, 10.5), Mag(1), Mag((1, 2, 2))],
        [(10.5, 10.5, 10.5), Mag(1), Mag((2, 2, 2))],
        [(10.5, 10.5, 24), Mag((2, 2, 1)), Mag((4, 4, 1))],
        [(10.5, 10.5, 21), Mag((2, 2, 1)), Mag((4, 4, 2))],
        [(10.5, 24, 10.5), Mag((2, 1, 2)), Mag((4, 1, 4))],
        [(24, 10.5, 10.5), Mag((1, 2, 2)), Mag((1, 4, 4))],
        [(10.5, 10.5, 10.5), Mag(2), Mag(4)],
        [(320, 320, 200), Mag(1), Mag((1, 1, 2))],
        [(320, 320, 200), Mag((1, 1, 2)), Mag((2, 2, 4))],
    ]

    for i in range(len(mag_tests)):
        next_mag = get_next_mag(mag_tests[i][1], mag_tests[i][0])
        assert mag_tests[i][2] == next_mag, (
            "The next anisotropic"
            f" Magnification of {mag_tests[i][1]} with "
            f"the size {mag_tests[i][0]} should be {mag_tests[i][2]} "
            f"and not {next_mag}"
        )


def test_downsampling_padding():
    ds_path = "./testoutput/larger_wk_dataset/"
    try:
        shutil.rmtree(ds_path)
    except:
        pass

    ds = WKDataset.create(ds_path, scale=(1, 1, 1))
    layer = ds.add_layer("layer1", "segmentation", num_channels=3, largest_segment_id=1000000000)
    mag1 = layer.add_mag("1", block_len=8, file_len=8)
    mag2 = layer.add_mag("2", block_len=8, file_len=8)

    # write some random data to mag 1 and mag 2
    mag1.write(
        offset=(10, 20, 30),
        data=(np.random.rand(3, 128, 148, 168) * 255).astype(np.uint8)
    )

    mag2.write(
        offset=(5, 10, 15),
        data=(np.random.rand(3, 64, 74, 84) * 255).astype(np.uint8)
    )

    assert np.array_equal(mag1.get_view().global_offset, (10, 20, 30))
    assert np.array_equal(mag1.get_view().size, (128, 148, 168))
    assert np.array_equal(mag2.get_view().global_offset, (5, 10, 15))
    assert np.array_equal(mag2.get_view().size, (64, 74, 84))

    layer.downsample(
        from_mag=Mag(2),
        max_mag=Mag(8),
        scale=(4, 2, 2),
        interpolation_mode="default",
        compress=False
    )

    # The data gets padded in a way that it is always possible to divide the offset and the size by 2
    assert np.array_equal(layer.get_mag("4-8-8").get_view().global_offset, (2, 2, 3))
    assert np.array_equal(layer.get_mag("2-4-4").get_view().global_offset, (4, 4, 6))
    assert np.array_equal(mag2.get_view().global_offset, (4, 8, 12))
    assert np.array_equal(mag1.get_view().global_offset, (8, 16, 24))
    # The 'end_offset' of the data in mag 1 is: (10, 20, 30) + (128, 148, 168) = (138, 168, 198)
    # Then 'end_offset' of mag 4-8-8 is: (35, 21, 25)  ->  size=(33, 19, 23)
    assert np.array_equal(layer.get_mag("4-8-8").get_view().size, (33, 19, 22))
    assert np.array_equal(layer.get_mag("2-4-4").get_view().size, (66, 38, 44))
    assert np.array_equal(mag2.get_view().size, (66, 76, 88))
    assert np.array_equal(mag1.get_view().size, (132, 152, 176))

