import logging
from math import ceil, floor

import geopandas as gpd
import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from tqdm import tqdm

log = logging.getLogger(__name__)


def solve_conflicts(
    cells: list[Polygon] | gpd.GeoDataFrame,
    threshold: float = 0.5,
    patch_indices: np.ndarray | None = None,
    return_indices: bool = False,
) -> gpd.GeoDataFrame | tuple[gpd.GeoDataFrame, np.ndarray]:
    """Resolve segmentation conflicts (i.e. overlap) after running segmentation on patches

    Args:
        cells: List of cell polygons
        threshold: When two cells are overlapping, we look at the area of intersection over the area of the smallest cell. If this value is higher than the `threshold`, the cells are merged
        patch_indices: Patch from which each cell belongs.
        return_indices: If `True`, returns also the cells indices. Merged cells have an index of -1.

    Returns:
        Array of resolved cells polygons. If `return_indices`, it also returns an array of cell indices.
    """
    cells = list(cells.geometry) if isinstance(cells, gpd.GeoDataFrame) else list(cells)
    n_cells = len(cells)
    resolved_indices = np.arange(n_cells)

    assert n_cells > 0, "No cells was segmented, cannot continue"

    tree = shapely.STRtree(cells)
    conflicts = tree.query(cells, predicate="intersects")

    if patch_indices is not None:
        conflicts = conflicts[:, patch_indices[conflicts[0]] != patch_indices[conflicts[1]]].T
    else:
        conflicts = conflicts[:, conflicts[0] != conflicts[1]].T

    for i1, i2 in tqdm(conflicts, desc="Resolving conflicts"):
        resolved_i1: int = resolved_indices[i1]
        resolved_i2: int = resolved_indices[i2]
        cell1, cell2 = cells[resolved_i1], cells[resolved_i2]

        intersection = cell1.intersection(cell2).area
        if intersection >= threshold * min(cell1.area, cell2.area):
            cell = _ensure_polygon(cell1.union(cell2))
            assert not cell.is_empty, "Merged cell is empty"

            resolved_indices[np.isin(resolved_indices, [resolved_i1, resolved_i2])] = len(cells)
            cells.append(cell)

    unique_indices = np.unique(resolved_indices)
    unique_cells = gpd.GeoDataFrame(geometry=cells).iloc[unique_indices]

    if return_indices:
        return unique_cells, np.where(unique_indices < n_cells, unique_indices, -1)

    return unique_cells


def _contours(cell_mask: np.ndarray) -> MultiPolygon:
    """Extract the contours of all cells from a binary mask

    Args:
        cell_mask: An array representing a cell: 1 where the cell is, 0 elsewhere

    Returns:
        A shapely MultiPolygon
    """
    import cv2

    contours, _ = cv2.findContours(cell_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return MultiPolygon([Polygon(contour[:, 0, :]) for contour in contours if contour.shape[0] >= 4])


def _ensure_polygon(cell: Polygon | MultiPolygon | GeometryCollection) -> Polygon:
    """Ensures that the provided cell becomes a Polygon

    Args:
        cell: A shapely Polygon or MultiPolygon or GeometryCollection

    Returns:
        The shape as a Polygon, or an empty Polygon if the cell was invalid
    """
    cell = shapely.make_valid(cell)

    if isinstance(cell, Polygon):
        if cell.interiors:
            cell = Polygon(list(cell.exterior.coords))
        return cell

    if isinstance(cell, MultiPolygon):
        return max(cell.geoms, key=lambda polygon: polygon.area)

    if isinstance(cell, GeometryCollection):
        geoms = [geom for geom in cell.geoms if isinstance(geom, Polygon)]

        if geoms:
            return max(geoms, key=lambda polygon: polygon.area)

        geoms = [geom for geom in cell.geoms if isinstance(geom, MultiPolygon)]
        geoms = [polygon for multi_polygon in geoms for polygon in multi_polygon.geoms]

        if geoms:
            return max(geoms, key=lambda polygon: polygon.area)

        log.warning(f"Removing cell of type {type(cell)} as it contains no Polygon geometry")
        return Polygon()

    log.warning(f"Removing cell of unknown type {type(cell)}")
    return Polygon()


def to_valid_polygons(geo_df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    geo_df.geometry = geo_df.geometry.map(_ensure_polygon)
    return geo_df[~geo_df.is_empty]


def _smoothen_cell(cell: MultiPolygon, smooth_radius: float, tolerance: float) -> Polygon:
    """Smoothen a cell polygon

    Args:
        cell_id: ID of the cell to geometrize
        smooth_radius: radius used to smooth the cell polygon
        tolerance: tolerance used to simplify the cell polygon

    Returns:
        Shapely polygon representing the cell, or an empty Polygon if the cell was empty after smoothing
    """
    cell = cell.buffer(-smooth_radius).buffer(2 * smooth_radius).buffer(-smooth_radius)
    cell = cell.simplify(tolerance)

    return _ensure_polygon(cell)


def _default_tolerance(mean_radius: float) -> float:
    if mean_radius < 10:
        return 0.4
    if mean_radius < 20:
        return 1
    return 2


def geometrize(mask: np.ndarray, tolerance: float | None = None, smooth_radius_ratio: float = 0.1) -> gpd.GeoDataFrame:
    """Convert a cells mask to multiple `shapely` geometries. Inspired from https://github.com/Vizgen/vizgen-postprocessing

    Args:
        mask: A cell mask. Non-null values correspond to cell ids
        tolerance: Tolerance parameter used by `shapely` during simplification. By default, define the tolerance automatically.

    Returns:
        GeoDataFrame of polygons representing each cell ID of the mask
    """
    max_cells = mask.max()

    if max_cells == 0:
        log.warning("No cell was returned by the segmentation")
        return []

    cells = gpd.GeoDataFrame(
        geometry=[_contours((mask == cell_id).astype("uint8")) for cell_id in range(1, max_cells + 1)]
    )

    mean_radius = np.sqrt(cells.area / np.pi).mean()
    smooth_radius = mean_radius * smooth_radius_ratio

    if tolerance is None:
        tolerance = _default_tolerance(mean_radius)

    cells.geometry = cells.geometry.map(lambda cell: _smoothen_cell(cell, smooth_radius, tolerance))
    cells = cells[~cells.is_empty]

    return cells


def pixel_outer_bounds(bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return [floor(bounds[0]), floor(bounds[1]), ceil(bounds[2]) + 1, ceil(bounds[3]) + 1]


def rasterize(cell: Polygon | MultiPolygon, shape: tuple[int, int], xy_min: tuple[int, int] = [0, 0]) -> np.ndarray:
    """Transform a cell polygon into a numpy array with value 1 where the polygon touches a pixel, else 0.

    Args:
        cell: Cell polygon to rasterize.
        shape: Image shape as a tuple (y, x).
        xy_min: Tuple containing the origin of the image [x0, y0].

    Returns:
        The mask array.
    """
    import cv2

    xmin, ymin, xmax, ymax = [xy_min[0], xy_min[1], xy_min[0] + shape[1], xy_min[1] + shape[0]]

    cell_translated = shapely.affinity.translate(cell, -xmin, -ymin)
    geoms = cell_translated.geoms if isinstance(cell_translated, MultiPolygon) else [cell_translated]

    rasterized_image = np.zeros((ymax - ymin, xmax - xmin), dtype=np.int8)

    for geom in geoms:
        coords = np.array(geom.exterior.coords)[None, :].astype(np.int32)
        cv2.fillPoly(rasterized_image, coords, color=1)

    return rasterized_image


def expand_radius(geo_df: gpd.GeoDataFrame, expand_radius_ratio: float | None) -> gpd.GeoDataFrame:
    if not expand_radius_ratio:
        return geo_df

    expand_radius_ = expand_radius_ratio * np.mean(np.sqrt(geo_df.area / np.pi))
    geo_df.geometry = geo_df.buffer(expand_radius_)
    return geo_df
