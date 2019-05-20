from argparse import ArgumentParser

import logging
import wkw
import os
import numpy as np
from scipy.misc import imsave
from typing import Tuple, Dict

from .metadata import read_metadata_for_layer
from .utils import add_verbose_flag, add_distribution_flags, get_executor_for_args
from .mag import Mag


def create_parser():
    parser = ArgumentParser()

    parser.add_argument("--source_path", "-s", help="Directory containing the wkw file.")

    parser.add_argument(
        "--destination_path", "-d", help="Output directory for the generated dataset."
    )

    parser.add_argument(
        "--layer_name",
        "-l",
        help="Name of the layer that will be converted to a tiff stack",
        default="color",
    )

    parser.add_argument("--name", "-n", help="Name of the tiffs", default=None)

    parser.add_argument("--axis", "-a", help="The axis that the image should be generated along. "
                                             "Thus choosing z will print x,y slices.", default="z")

    parser.add_argument("--bbox", "-b", help="The BoundingBox of which the tiff stack should be generated.", default=None)

    parser.add_argument("--mag", "-m", help="The magnification that should be read", default=1)

    add_verbose_flag(parser)
    add_distribution_flags(parser)

    return parser

def export_tiff_slice(export_args: Tuple[int, Dict[str, Tuple[int, int, int]], str, str, str, wkw.Dataset]):
    axis_index, bbox, dest_path, name, axis, dataset = export_args
    tiff_bbox = bbox

    if axis == "x":
        tiff_bbox["topleft"] = [tiff_bbox["topleft"][0] + axis_index, tiff_bbox["topleft"][1], tiff_bbox["topleft"][2]]
        tiff_bbox["size"] = [1, tiff_bbox["size"][1], tiff_bbox["size"][2]]
    if axis == "y":
        tiff_bbox["topleft"] = [tiff_bbox["topleft"][0], tiff_bbox["topleft"][1] + axis_index, tiff_bbox["topleft"][2]]
        tiff_bbox["size"] = [tiff_bbox["size"][0], 1, tiff_bbox["size"][2]]
    else:
        tiff_bbox["topleft"] = [tiff_bbox["topleft"][0], tiff_bbox["topleft"][1], tiff_bbox["topleft"][2] + axis_index]
        tiff_bbox["size"] = [tiff_bbox["size"][0], tiff_bbox["size"][1], 1]

    tiff_file_name = f'name_topleft_{tiff_bbox["topleft"][0]}_{tiff_bbox["topleft"][1]}_{tiff_bbox["topleft"][2]}' \
                f'size_{tiff_bbox["topleft"][0]}_{tiff_bbox["topleft"][1]}_{tiff_bbox["topleft"][2]}.tiff'
    tiff_file_path = os.path.join(dest_path, tiff_file_name)

    tiff_data = dataset.read(tiff_bbox["topleft"], tiff_bbox["size"])
    tiff_data = np.squeeze(tiff_data)

    imsave(tiff_file_path, tiff_data)



def export_tiff_stack(wkw_file_path, wkw_layer, bbox, mag,  destination_path, name, axis, args):
    if not os.path.isdir(destination_path):
        os.mkdir(destination_path)

    dataset_path = os.join(wkw_file_path, wkw_layer, mag.to_layer_name())
    with wkw.Dataset.open(dataset_path) as dataset:
        with get_executor_for_args(args) as executor:
            if axis == "x":
                axis_index = 0
            elif axis == "y":
                axis_index = 1
            else:
                axis_index = 2
            num_slices = bbox["size"][axis_index]
            slices = range(num_slices)
            export_args = zip(slices, list(bbox) * num_slices, list(destination_path) * num_slices,
                              list(name) * num_slices, list(axis) * num_slices, list(dataset) * num_slices)
            executor.map(export_tiff_slice, export_args)



if __name__ == "__main__":
    args = create_parser().parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    assert args.axis in ("x", "y", "z"), "The axis needs to be x, y or z."
    if args.bbox == None:
        _, _, bbox, _ = read_metadata_for_layer(args.source_path, args.layer_name)
    else:
        bbox = ([int(s.strip()) for s in args.bbox.split(",")])
        assert len(bbox) == 6
        bbox = {"topleft" : bbox[0:3],
                "size" : bbox[3:6]}

    export_tiff_stack(wkw_file_path=args.source_path, wkw_layer=args.layer_name, bbox=bbox, mag=Mag(args.mag),
                      destination_path=args.destination_path, name=args.name, axis=args.axis, args=args)