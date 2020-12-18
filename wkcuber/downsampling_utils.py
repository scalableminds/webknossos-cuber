import copy
import logging
import math
from enum import Enum
from itertools import product
from typing import Optional, Tuple

import numpy as np
from scipy.ndimage import zoom

from wkcuber.mag import Mag
from wkcuber.utils import time_start, time_stop


class InterpolationModes(Enum):
    MEDIAN = 0
    MODE = 1
    NEAREST = 2
    BILINEAR = 3
    BICUBIC = 4
    MAX = 5
    MIN = 6


DEFAULT_EDGE_LEN = 256


def determine_buffer_edge_len(dataset):
    if hasattr(dataset.header, 'file_len') and hasattr(dataset.header, 'block_len'):
        return min(DEFAULT_EDGE_LEN, dataset.header.file_len * dataset.header.block_len)
    return DEFAULT_EDGE_LEN


def detect_larger_and_smaller_dimension(scale):
    scale_np = np.array(scale)
    return np.argmax(scale_np), np.argmin(scale_np)


def get_next_mag(mag, scale: Optional[Tuple[float, float, float]]) -> Mag:
    if scale is None:
        return mag.scaled_by(2)
    else:
        max_index, min_index = detect_larger_and_smaller_dimension(scale)
        mag_array = mag.to_array()
        scale_increase = [1, 1, 1]

        if (
            mag_array[min_index] * scale[min_index]
            < mag_array[max_index] * scale[max_index]
        ):
            for i in range(len(scale_increase)):
                scale_increase[i] = 1 if scale[i] == scale[max_index] else 2
        else:
            scale_increase = [2, 2, 2]
        return Mag(
            [
                mag_array[0] * scale_increase[0],
                mag_array[1] * scale_increase[1],
                mag_array[2] * scale_increase[2],
            ]
        )


def parse_interpolation_mode(interpolation_mode, layer_name):
    if interpolation_mode.upper() == "DEFAULT":
        return (
            InterpolationModes.MEDIAN
            if layer_name == "color"
            else InterpolationModes.MODE
        )
    else:
        return InterpolationModes[interpolation_mode.upper()]


def linear_filter_3d(data, factors, order):
    factors = np.array(factors)

    if not np.all(factors == factors[0]):
        logging.debug(
            "the selected filtering strategy does not support anisotropic downsampling. Selecting {} as uniform downsampling factor".format(
                factors[0]
            )
        )
    factor = factors[0]

    ds = data.shape
    assert not any((d % factor > 0 for d in ds))
    return zoom(
        data,
        1 / factor,
        output=data.dtype,
        # 0: nearest
        # 1: bilinear
        # 2: bicubic
        order=order,
        # this does not mean nearest interpolation,
        # it corresponds to how the borders are treated.
        mode="nearest",
        prefilter=True,
    )


def non_linear_filter_3d(data, factors, func):
    ds = data.shape
    assert not any((d % factor > 0 for (d, factor) in zip(ds, factors)))
    data = data.reshape((ds[0], factors[1], ds[1] // factors[1], ds[2]), order="F")
    data = data.swapaxes(0, 1)
    data = data.reshape(
        (
            factors[0] * factors[1],
            ds[0] * ds[1] // (factors[0] * factors[1]),
            factors[2],
            ds[2] // factors[2],
        ),
        order="F",
    )
    data = data.swapaxes(2, 1)
    data = data.reshape(
        (
            factors[0] * factors[1] * factors[2],
            (ds[0] * ds[1] * ds[2]) // (factors[0] * factors[1] * factors[2]),
        ),
        order="F",
    )
    data = func(data)
    data = data.reshape(
        (ds[0] // factors[0], ds[1] // factors[1], ds[2] // factors[2]), order="F"
    )
    return data


def _max(x):
    return np.max(x, axis=0)


def _min(x):
    return np.min(x, axis=0)


def _median(x):
    return np.median(x, axis=0).astype(x.dtype)


def _mode(x):
    """
    Fast mode implementation from: https://stackoverflow.com/a/35674754
    """
    # Check inputs
    ndim = x.ndim
    axis = 0
    # Sort array
    sort = np.sort(x, axis=axis)
    # Create array to transpose along the axis and get padding shape
    transpose = np.roll(np.arange(ndim)[::-1], axis)
    shape = list(sort.shape)
    shape[axis] = 1
    # Create a boolean array along strides of unique values
    strides = (
        np.concatenate(
            [
                np.zeros(shape=shape, dtype="bool"),
                np.diff(sort, axis=axis) == 0,
                np.zeros(shape=shape, dtype="bool"),
            ],
            axis=axis,
        )
        .transpose(transpose)
        .ravel()
    )
    # Count the stride lengths
    counts = np.cumsum(strides)
    counts[~strides] = np.concatenate([[0], np.diff(counts[~strides])])
    counts[strides] = 0
    # Get shape of padded counts and slice to return to the original shape
    shape = np.array(sort.shape)
    shape[axis] += 1
    shape = shape[transpose]
    slices = [slice(None)] * ndim
    slices[axis] = slice(1, None)
    # Reshape and compute final counts
    counts = counts.reshape(shape).transpose(transpose)[tuple(slices)] + 1

    # Find maximum counts and return modals/counts
    slices = [slice(None, i) for i in sort.shape]
    del slices[axis]
    index = np.ogrid[slices]
    index.insert(axis, np.argmax(counts, axis=axis))
    return sort[tuple(index)]


def downsample_cube(cube_buffer, factors, interpolation_mode):
    if interpolation_mode == InterpolationModes.MODE:
        return non_linear_filter_3d(cube_buffer, factors, _mode)
    elif interpolation_mode == InterpolationModes.MEDIAN:
        return non_linear_filter_3d(cube_buffer, factors, _median)
    elif interpolation_mode == InterpolationModes.NEAREST:
        return linear_filter_3d(cube_buffer, factors, 0)
    elif interpolation_mode == InterpolationModes.BILINEAR:
        return linear_filter_3d(cube_buffer, factors, 1)
    elif interpolation_mode == InterpolationModes.BICUBIC:
        return linear_filter_3d(cube_buffer, factors, 2)
    elif interpolation_mode == InterpolationModes.MAX:
        return non_linear_filter_3d(cube_buffer, factors, _max)
    elif interpolation_mode == InterpolationModes.MIN:
        return non_linear_filter_3d(cube_buffer, factors, _min)
    else:
        raise Exception("Invalid interpolation mode: {}".format(interpolation_mode))


def downsample_cube_job(args):
    (
        target_view,
        (
            source_view,
            mag_factors,
            interpolation_mode,
            buffer_edge_len,
            compress,
            chunck_size,
            use_logging,
        )
    ) = args

    # Build the target view
    # The blueprint view contains information about the header and the path
    # By passing it as a view, this method does not need to know whether it is a WKView or a TiffView
    source_cube_xyz = tuple(dim * mag_factor for (dim, mag_factor) in zip(target_view.global_offset, mag_factors))
    source_cube_size = tuple(dim * mag_factor for (dim, mag_factor) in zip(target_view.size, mag_factors))
    source_view.global_offset = source_cube_xyz
    source_view.size = source_cube_size
    print(target_view)

    if use_logging:
        logging.info("Downsampling of {}".format(target_view.global_offset))

    try:
        if use_logging:
            time_start("Downsampling of {}".format(target_view.global_offset))

        #wkw_cubelength = (
        #            source_view.header.file_len * source_view.header.block_len # TODO
        #        )
        num_channels = target_view.header.num_channels
        shape = (num_channels,) + tuple(target_view.size)
        file_buffer = np.zeros(shape, target_view.get_dtype())

        #assert (
        #    wkw_cubelength % buffer_edge_len == 0
        #), "buffer_cube_size must be a divisor of wkw cube length"

        #tile_indices = list(range(0, wkw_cubelength // buffer_edge_len))
        #tile_indices = list(range(0, target_view.size // buffer_edge_len))
        #tiles = product(tile_indices, tile_indices, tile_indices)
        #tiles = product(*list([list(range(0, len)) for len in np.array(chunck_size) // buffer_edge_len]))
        tiles = product(*list([list(range(0, math.ceil(len))) for len in np.array(chunck_size) / buffer_edge_len]))

        source_view.open()

        for tile in tiles:
            target_offset = np.array(
                tile
            ) * buffer_edge_len
            source_offset = mag_factors * target_offset

            # Read source buffer
            cube_buffer_channels = source_view.read(
                source_offset,
                np.minimum(buffer_edge_len, source_view.size),
            )

            for channel_index in range(num_channels):
                cube_buffer = cube_buffer_channels[channel_index]

                if not np.all(cube_buffer == 0):
                    # Downsample the buffer
                    data_cube = downsample_cube(
                        cube_buffer, mag_factors, interpolation_mode
                    )

                    buffer_offset = target_offset
                    buffer_end = buffer_offset + buffer_edge_len

                    file_buffer[
                        channel_index,
                        buffer_offset[0]: buffer_end[0],
                        buffer_offset[1]: buffer_end[1],
                        buffer_offset[2]: buffer_end[2],
                    ] = data_cube

        source_view.close()
        # Write the downsampled buffer to target
        if source_view.header.num_channels == 1:
            file_buffer = file_buffer[0]  # remove channel dimension
        target_view.write(file_buffer)
        if use_logging:
            time_stop("Downsampling of {}".format(target_view.global_offset))

    except Exception as exc:
        logging.error("Downsampling of {} failed with {}".format(target_view.global_offset, exc))
        raise exc

