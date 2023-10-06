import json
import logging
from pathlib import Path

import geopandas as gpd
from spatialdata import SpatialData

from ..._sdata import get_boundaries, get_element, get_spatial_image, to_intrinsic
from . import (
    write_cell_categories,
    write_gene_counts,
    write_image,
    write_polygons,
    write_transcripts,
)
from ._constants import FileNames, experiment_dict

log = logging.getLogger(__name__)


def _check_explorer_directory(path: Path):
    assert (
        not path.exists() or path.is_dir()
    ), f"A path to an existing file was provided. It should be a path to a directory."
    path.mkdir(parents=True, exist_ok=True)


def write_explorer(
    path: str,
    sdata: SpatialData,
    image_key: str | None = None,
    gene_column: str | None = None,
    points_key: str | None = None,
    layer: str | None = None,
    polygon_max_vertices: int = 13,
    lazy: bool = True,
    ram_threshold: int | None = None,
    save_image_mode: int = 1,
) -> None:
    """
    Transform a SpatialData object into inputs for the Xenium Explorer.
    Currently only images of type MultiscaleSpatialImage are supported.

    Args:
        path: Path to the directory where files will be saved.
        sdata: SpatialData object.
        image_key: Name of the image of interest (key of `sdata.images`).
        shapes_key: Name of the cell shapes (key of `sdata.shapes`).
        points_key: Name of the transcripts (key of `sdata.points`).
        gene_column: Column name of the points dataframe containing the gene names.
        layer: Layer of `sdata.table` where the gene counts are saved. If `None`, uses `sdata.table.X`.
        polygon_max_vertices: Maximum number of vertices for the cell polygons.
    """
    path: Path = Path(path)
    _check_explorer_directory(path)

    image_key, image = get_spatial_image(sdata, image_key, return_key=True)

    if save_image_mode == 2:
        log.info(f"{save_image_mode=} (only the image will be saved)")
        write_image(path / FileNames.IMAGE, image, lazy=lazy, ram_threshold=ram_threshold)
        return

    ### Saving cell categories and gene counts
    if sdata.table is not None:
        adata = sdata.table
        shapes_key = adata.uns["spatialdata_attrs"]["region"]
        geo_df = sdata[shapes_key][adata.obs[adata.uns["spatialdata_attrs"]["instance_key"]].values]

        write_gene_counts(path / FileNames.TABLE, adata, layer)
        write_cell_categories(path / FileNames.CELL_CATEGORIES, adata)
    else:
        shapes_key, geo_df = get_boundaries(sdata, return_key=True)

    ### Saving cell boundaries
    if geo_df is not None:
        geo_df = to_intrinsic(sdata, geo_df, image)
        write_polygons(path / FileNames.SHAPES, geo_df.geometry, polygon_max_vertices)

    ### Saving transcripts
    df = get_element(sdata, "points", points_key)
    if df is not None:
        assert (
            gene_column is not None
        ), "The argument 'gene_column' has to be provided to save the transcripts"
        df = to_intrinsic(sdata, df, image)
        write_transcripts(path / FileNames.POINTS, df, gene_column)

    ### Saving image
    if save_image_mode:
        write_image(path / FileNames.IMAGE, image, lazy=lazy, ram_threshold=ram_threshold)
    else:
        log.info(f"{save_image_mode:=} (the image will not be saved)")

    ### Saving experiment.xenium file
    with open(path / FileNames.METADATA, "w") as f:
        EXPERIMENT = experiment_dict(image_key, shapes_key, _get_n_obs(sdata, geo_df))
        json.dump(EXPERIMENT, f, indent=4)


def _get_n_obs(sdata: SpatialData, geo_df: gpd.GeoDataFrame) -> int:
    if sdata.table is not None:
        return sdata.table.n_obs
    return len(geo_df) if geo_df is not None else 0
