from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from spatialdata import SpatialData

from ... import settings
from ..._constants import ATTRS_KEY, SopaAttrs, SopaFiles, SopaKeys
from ...utils import get_transcripts_patches_dirs
from .._transcripts import copy_segmentation_config, resolve

log = logging.getLogger(__name__)


def baysor(
    sdata: SpatialData,
    config: dict | str | None = None,
    min_area: int = 0,
    delete_cache: bool = True,
    recover: bool = False,
    force: bool = False,
    key_added: str = SopaKeys.BAYSOR_BOUNDARIES,
):
    assert (
        SopaKeys.TRANSCRIPT_PATCHES in sdata.shapes
    ), "Transcript patches not found in the SpatialData object. Run `sopa.make_transcript_patches(...)` first."

    import shutil

    baysor_executable_path = _get_baysor_executable_path()
    use_polygons_format_argument = _use_polygons_format_argument(baysor_executable_path)

    if config is None:
        log.info("No config provided, inferring a default Baysor config.")
        config = _get_default_config(sdata)

    if isinstance(config, str):
        import toml

        config = toml.load(config)

    assert config.get("data", {}).get("gene"), "Gene column not found in config['data']['gene']"
    gene_column = config["data"]["gene"]

    patches_dirs = get_transcripts_patches_dirs(sdata)

    for patch_dir in patches_dirs:
        copy_segmentation_config(patch_dir / SopaFiles.TOML_CONFIG_FILE, config)

    prior_shapes_key = None
    if SopaKeys.PRIOR_SHAPES_KEY in sdata.shapes[SopaKeys.TRANSCRIPT_PATCHES]:
        prior_shapes_key = sdata.shapes[SopaKeys.TRANSCRIPT_PATCHES][SopaKeys.PRIOR_SHAPES_KEY].iloc[0]

    baysor_patch = BaysorPatch(
        baysor_executable_path,
        use_polygons_format_argument,
        force=force,
        recover=recover,
        prior_shapes_key=prior_shapes_key,
    )

    settings._run_with_backend([partial(baysor_patch, patch_dir) for patch_dir in patches_dirs])

    if force:
        assert any(
            (patch_dir / "segmentation_counts.loom").exists() for patch_dir in patches_dirs
        ), "Baysor failed on all patches"

    resolve(sdata, patches_dirs, gene_column, min_area=min_area, key_added=key_added)

    sdata.attrs[SopaAttrs.BOUNDARIES] = key_added

    if delete_cache:
        for patch_dir in patches_dirs:
            shutil.rmtree(patch_dir)


class BaysorPatch:
    def __init__(
        self,
        baysor_executable_path: str,
        use_polygons_format_argument: bool,
        force: bool = False,
        recover: bool = False,
        prior_shapes_key: str | None = None,
    ):
        self.baysor_executable_path = baysor_executable_path
        self.use_polygons_format_argument = use_polygons_format_argument
        self.force = force
        self.recover = recover
        self.prior_shapes_key = prior_shapes_key

    def __call__(self, patch_dir: Path):
        if self.recover and (patch_dir / "segmentation_counts.loom").exists():
            return

        import subprocess

        polygon_substring = (
            "--polygon-format GeometryCollection" if self.use_polygons_format_argument else "--save-polygons GeoJSON"
        )

        prior_suffix = f":{self.prior_shapes_key}" if self.prior_shapes_key else ""

        baysor_command = (
            f"{self.baysor_executable_path} run {polygon_substring} -c config.toml transcripts.csv {prior_suffix}"
        )

        result = subprocess.run(
            f"""
            cd {patch_dir}
            {baysor_command}
            """,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if result.returncode != 0:
            message = f"Baysor error on patch {patch_dir} with command `{baysor_command}`"
            if self.force:
                log.warning(message)
                return
            raise RuntimeError(f"{message}:\n{result.stdout.decode()}")


def _use_polygons_format_argument(baysor_executable_path: str) -> bool:
    import subprocess

    from packaging.version import Version

    try:
        res = subprocess.run(
            f"{baysor_executable_path} --version",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        return Version(res.stdout) >= Version("0.7.0")
    except:
        return False


def _get_baysor_executable_path() -> Path | str:
    import shutil

    if shutil.which("baysor") is not None:
        return "baysor"

    default_path = Path.home() / ".julia" / "bin" / "baysor"
    if default_path.exists():
        return default_path

    raise FileNotFoundError(
        f"Please install baysor and ensure that either `{default_path}` executes baysor, or `baysor` is an existing shell alias for baysor's executable."
    )


def _get_default_config(sdata: SpatialData) -> dict:
    points_key = sdata.attrs.get(SopaAttrs.TRANSCRIPTS)
    assert (
        points_key
    ), f"Transcripts key not found in sdata.attrs['{SopaAttrs.TRANSCRIPTS}'], baysor config can't be inferred."

    feature_key = sdata[points_key].attrs[ATTRS_KEY].get("feature_key")
    assert (
        feature_key
    ), f"Feature key not found in sdata['{points_key}'].attrs['{ATTRS_KEY}'], baysor config can't be inferred."

    return {
        "data": {
            "x": "x",
            "y": "y",
            "gene": feature_key,
            "min_molecules_per_gene": 10,
            "min_molecules_per_cell": 20,
            "force_2d": True,
        },
        "segmentation": {"prior_segmentation_confidence": 0.8},
    }
