# Licensed under a 3-clause BSD style license - see LICENSE.rst
import pytest
import numpy as np
from numpy.testing import assert_allclose
import astropy.units as u
from astropy.coordinates import SkyCoord
from regions import CircleSkyRegion
from gammapy.data import GTI
from gammapy.datasets import Datasets, MapDataset, MapDatasetOnOff
from gammapy.irf import (
    EDispMap,
    EffectiveAreaTable2D,
    EnergyDependentMultiGaussPSF,
    PSFMap,
)
from gammapy.makers.utils import make_map_exposure_true_energy
from gammapy.maps import Map, MapAxis, WcsGeom, WcsNDMap
from gammapy.modeling import Fit
from gammapy.modeling.models import (
    BackgroundModel,
    GaussianSpatialModel,
    Models,
    PowerLawSpectralModel,
    SkyModel,
)
from gammapy.utils.testing import mpl_plot_check, requires_data, requires_dependency


@pytest.fixture
def geom():
    axis = MapAxis.from_energy_bounds("0.1 TeV", "10 TeV", nbin=2)
    return WcsGeom.create(
        skydir=(266.40498829, -28.93617776),
        binsz=0.02,
        width=(2, 2),
        frame="icrs",
        axes=[axis],
    )


@pytest.fixture
def geom_etrue():
    axis = MapAxis.from_energy_bounds("0.1 TeV", "10 TeV", nbin=3, name="energy_true")
    return WcsGeom.create(
        skydir=(266.40498829, -28.93617776),
        binsz=0.02,
        width=(2, 2),
        frame="icrs",
        axes=[axis],
    )


@pytest.fixture
def geom_image():
    ebounds_true = np.logspace(-1.0, 1.0, 2)
    axis = MapAxis.from_edges(ebounds_true, name="energy", unit=u.TeV, interp="log")
    return WcsGeom.create(
        skydir=(0, 0), binsz=0.02, width=(2, 2), frame="galactic", axes=[axis]
    )


def get_exposure(geom_etrue):
    filename = (
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    aeff = EffectiveAreaTable2D.read(filename, hdu="EFFECTIVE AREA")

    exposure_map = make_map_exposure_true_energy(
        pointing=SkyCoord(1, 0.5, unit="deg", frame="galactic"),
        livetime="1 hour",
        aeff=aeff,
        geom=geom_etrue,
    )
    return exposure_map


def get_psf():
    filename = (
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    psf = EnergyDependentMultiGaussPSF.read(filename, hdu="POINT SPREAD FUNCTION")

    table_psf = psf.to_energy_dependent_table_psf(theta=0.5 * u.deg)
    psf_map = PSFMap.from_energy_dependent_table_psf(table_psf)
    return psf_map


@pytest.fixture
def sky_model():
    spatial_model = GaussianSpatialModel(
        lon_0="0.2 deg", lat_0="0.1 deg", sigma="0.2 deg", frame="galactic"
    )
    spectral_model = PowerLawSpectralModel(
        index=3, amplitude="1e-11 cm-2 s-1 TeV-1", reference="1 TeV"
    )
    return SkyModel(spatial_model=spatial_model, spectral_model=spectral_model)


def get_map_dataset(sky_model, geom, geom_etrue, edisp=True, name="test", **kwargs):
    """Returns a MapDatasets"""
    # define background model
    m = Map.from_geom(geom)
    m.quantity = 0.2 * np.ones(m.data.shape)
    background_model = BackgroundModel(m, datasets_names=[name])

    psf = get_psf()
    exposure = get_exposure(geom_etrue)

    if edisp:
        # define energy dispersion
        e_true = geom_etrue.get_axis_by_name("energy_true")
        edisp = EDispMap.from_diagonal_response(energy_axis_true=e_true)
    else:
        edisp = None

    # define fit mask
    center = sky_model.spatial_model.position
    circle = CircleSkyRegion(center=center, radius=1 * u.deg)
    mask_fit = background_model.map.geom.region_mask([circle])
    mask_fit = Map.from_geom(geom, data=mask_fit)

    return MapDataset(
        models=[sky_model, background_model],
        exposure=exposure,
        psf=psf,
        edisp=edisp,
        mask_fit=mask_fit,
        name=name,
        **kwargs
    )


@requires_data()
def test_map_dataset_str(sky_model, geom, geom_etrue):
    dataset = get_map_dataset(sky_model, geom, geom_etrue)
    dataset.counts = dataset.npred()
    dataset.mask_safe = dataset.mask_fit
    assert "MapDataset" in str(dataset)
    assert "(frozen)" in str(dataset)
    assert "background" in str(dataset)

    dataset.mask_safe = None
    assert "MapDataset" in str(dataset)


@requires_data()
def test_fake(sky_model, geom, geom_etrue):
    """Test the fake dataset"""
    dataset = get_map_dataset(sky_model, geom, geom_etrue)
    npred = dataset.npred()
    assert np.all(npred.data >= 0)  # npred must be positive
    dataset.counts = npred
    real_dataset = dataset.copy()
    dataset.fake(314)

    assert real_dataset.counts.data.shape == dataset.counts.data.shape
    assert_allclose(real_dataset.counts.data.sum(), 9525.755807)
    assert_allclose(dataset.counts.data.sum(), 9723)


@pytest.mark.xfail
@requires_data()
def test_different_exposure_unit(sky_model, geom):
    dataset_ref = get_map_dataset(sky_model, geom, geom, edisp=False)
    npred_ref = dataset_ref.npred()

    ebounds_true = np.logspace(2, 4, 3)
    axis = MapAxis.from_edges(ebounds_true, name="energy", unit="GeV", interp="log")
    geom_gev = WcsGeom.create(
        skydir=(266.40498829, -28.93617776),
        binsz=0.02,
        width=(2, 2),
        frame="icrs",
        axes=[axis],
    )

    dataset = get_map_dataset(sky_model, geom, geom_gev, edisp=False)
    npred = dataset.npred()

    assert_allclose(npred.data[0, 50, 50], npred_ref.data[0, 50, 50])


@requires_data()
def test_to_spectrum_dataset(sky_model, geom, geom_etrue):
    dataset_ref = get_map_dataset(sky_model, geom, geom_etrue, edisp=True)

    dataset_ref.counts = dataset_ref.background_model.map * 0.0
    dataset_ref.counts.data[1, 50, 50] = 1
    dataset_ref.counts.data[1, 60, 50] = 1

    gti = GTI.create([0 * u.s], [1 * u.h], reference_time="2010-01-01T00:00:00")
    dataset_ref.gti = gti
    on_region = CircleSkyRegion(center=geom.center_skydir, radius=0.05 * u.deg)
    spectrum_dataset = dataset_ref.to_spectrum_dataset(on_region)
    spectrum_dataset_corrected = dataset_ref.to_spectrum_dataset(
        on_region, containment_correction=True
    )

    assert np.sum(spectrum_dataset.counts.data) == 1
    assert spectrum_dataset.data_shape == (2, 1, 1)
    assert spectrum_dataset.background.geom.axes[0].nbin == 2
    assert spectrum_dataset.aeff.energy.nbin == 3
    assert spectrum_dataset.aeff.data.data.unit == "m2"
    assert spectrum_dataset.edisp.e_reco.nbin == 2
    assert spectrum_dataset.edisp.e_true.nbin == 3
    assert spectrum_dataset_corrected.aeff.data.data.unit == "m2"
    assert_allclose(spectrum_dataset.aeff.data.data.value[1], 853023.423047, rtol=1e-5)
    assert_allclose(
        spectrum_dataset_corrected.aeff.data.data.value[1], 559476.3357, rtol=1e-5
    )


@requires_data()
def test_to_image(geom):

    counts = Map.read("$GAMMAPY_DATA/fermi-3fhl-gc/fermi-3fhl-gc-counts-cube.fits.gz")
    background = Map.read(
        "$GAMMAPY_DATA/fermi-3fhl-gc/fermi-3fhl-gc-background-cube.fits.gz"
    )
    background = BackgroundModel(background, datasets_names=["fermi"])

    exposure = Map.read(
        "$GAMMAPY_DATA/fermi-3fhl-gc/fermi-3fhl-gc-exposure-cube.fits.gz"
    )
    exposure = exposure.sum_over_axes(keepdims=True)
    dataset = MapDataset(
        counts=counts, models=[background], exposure=exposure, name="fermi"
    )
    dataset_im = dataset.to_image()
    assert dataset_im.mask_safe is None
    assert dataset_im.counts.data.sum() == dataset.counts.data.sum()
    assert_allclose(dataset_im.background_model.map.data.sum(), 28548.625, rtol=1e-5)

    ebounds = np.logspace(-1.0, 1.0, 3)
    axis = MapAxis.from_edges(ebounds, name="energy", unit=u.TeV, interp="log")
    geom = WcsGeom.create(
        skydir=(0, 0), binsz=0.5, width=(1, 1), frame="icrs", axes=[axis]
    )
    dataset = MapDataset.create(geom)

    # Check map_safe handling
    data = np.array([[[False, True], [True, True]], [[False, False], [True, True]]])
    dataset.mask_safe = WcsNDMap.from_geom(geom=geom, data=data)

    dataset_im = dataset.to_image()
    assert dataset_im.mask_safe.data.dtype == bool

    desired = np.array([[False, True], [True, True]])
    assert (dataset_im.mask_safe.data == desired).all()

    # Check that missing entries in the dataset do not break
    dataset_copy = dataset.copy()
    dataset_copy.exposure = None
    dataset_copy.background_model = None
    dataset_im = dataset_copy.to_image()
    assert dataset_im.exposure is None
    assert dataset_im.background_model is None

    dataset_copy = dataset.copy()
    dataset_copy.counts = None
    dataset_im = dataset_copy.to_image()
    assert dataset_im.counts is None


@requires_data()
def test_map_dataset_fits_io(tmp_path, sky_model, geom, geom_etrue):
    dataset = get_map_dataset(sky_model, geom, geom_etrue)
    dataset.counts = dataset.npred()
    dataset.mask_safe = dataset.mask_fit
    gti = GTI.create([0 * u.s], [1 * u.h], reference_time="2010-01-01T00:00:00")
    dataset.gti = gti

    hdulist = dataset.to_hdulist()
    actual = [hdu.name for hdu in hdulist]

    desired = [
        "PRIMARY",
        "COUNTS",
        "COUNTS_BANDS",
        "EXPOSURE",
        "EXPOSURE_BANDS",
        "BACKGROUND",
        "BACKGROUND_BANDS",
        "EDISP",
        "EDISP_BANDS",
        "EDISP_EXPOSURE",
        "EDISP_EXPOSURE_BANDS",
        "PSF",
        "PSF_BANDS",
        "PSF_EXPOSURE",
        "PSF_EXPOSURE_BANDS",
        "MASK_SAFE",
        "MASK_SAFE_BANDS",
        "MASK_FIT",
        "MASK_FIT_BANDS",
        "GTI",
    ]

    assert actual == desired

    dataset.write(tmp_path / "test.fits")

    dataset_new = MapDataset.read(tmp_path / "test.fits")
    assert len(dataset_new.models) == 1
    assert dataset_new.mask.dtype == bool

    assert_allclose(dataset.counts.data, dataset_new.counts.data)
    assert_allclose(
        dataset.background_model.map.data, dataset_new.background_model.map.data
    )
    assert_allclose(dataset.edisp.edisp_map.data, dataset_new.edisp.edisp_map.data)
    assert_allclose(dataset.psf.psf_map.data, dataset_new.psf.psf_map.data)
    assert_allclose(dataset.exposure.data, dataset_new.exposure.data)
    assert_allclose(dataset.mask_fit.data, dataset_new.mask_fit.data)
    assert_allclose(dataset.mask_safe.data, dataset_new.mask_safe.data)

    assert dataset.counts.geom == dataset_new.counts.geom
    assert dataset.exposure.geom == dataset_new.exposure.geom
    assert dataset.background_model.map.geom == dataset_new.background_model.map.geom
    assert dataset.edisp.edisp_map.geom == dataset_new.edisp.edisp_map.geom

    assert_allclose(
        dataset.gti.time_sum.to_value("s"), dataset_new.gti.time_sum.to_value("s")
    )

    # To test io of psf and edisp map
    stacked = MapDataset.create(geom)
    stacked.write("test.fits", overwrite=True)
    stacked1 = MapDataset.read("test.fits")
    assert stacked1.psf.psf_map is not None
    assert stacked1.psf.exposure_map is not None
    assert stacked1.edisp.edisp_map is not None
    assert stacked1.edisp.exposure_map is not None
    assert stacked.mask.dtype == bool

    assert_allclose(stacked1.psf.psf_map, stacked.psf.psf_map)
    assert_allclose(stacked1.edisp.edisp_map, stacked.edisp.edisp_map)


@requires_dependency("iminuit")
@requires_dependency("matplotlib")
@requires_data()
def test_map_fit(sky_model, geom, geom_etrue):
    dataset_1 = get_map_dataset(
        sky_model, geom, geom_etrue, evaluation_mode="local", name="test-1"
    )
    dataset_1.background_model.norm.value = 0.5
    dataset_1.counts = dataset_1.npred()

    dataset_2 = get_map_dataset(
        sky_model, geom, geom_etrue, evaluation_mode="global", name="test-2"
    )
    dataset_2.counts = dataset_2.npred()

    sky_model.parameters["sigma"].frozen = True

    dataset_1.background_model.norm.value = 0.49
    dataset_2.background_model.norm.value = 0.99

    fit = Fit([dataset_1, dataset_2])
    result = fit.run()

    assert result.success
    assert "minuit" in repr(result)

    npred = dataset_1.npred().data.sum()
    assert_allclose(npred, 7525.790688, rtol=1e-3)
    assert_allclose(result.total_stat, 21700.253246, rtol=1e-3)

    pars = result.parameters
    assert_allclose(pars["lon_0"].value, 0.2, rtol=1e-2)
    assert_allclose(pars["lon_0"].error, 0.002244, rtol=1e-2)

    assert_allclose(pars["index"].value, 3, rtol=1e-2)
    assert_allclose(pars["index"].error, 0.024277, rtol=1e-2)

    assert_allclose(pars["amplitude"].value, 1e-11, rtol=1e-2)
    assert_allclose(pars["amplitude"].error, 4.216154e-13, rtol=1e-2)

    # background norm 1
    assert_allclose(pars[8].value, 0.5, rtol=1e-2)
    assert_allclose(pars[8].error, 0.015811, rtol=1e-2)

    # background norm 2
    assert_allclose(pars[11].value, 1, rtol=1e-2)
    assert_allclose(pars[11].error, 0.02147, rtol=1e-2)

    # test mask_safe evaluation
    mask_safe = geom.energy_mask(emin=1 * u.TeV)
    dataset_1.mask_safe = Map.from_geom(geom, data=mask_safe)
    dataset_2.mask_safe = Map.from_geom(geom, data=mask_safe)

    stat = fit.datasets.stat_sum()
    assert_allclose(stat, 14824.282955)

    # test model evaluation outside image

    dataset_1.models[0].spatial_model.lon_0.value = 150
    dataset_1.npred()
    assert not dataset_1._evaluators[dataset_1.models[0].name].contributes

    with mpl_plot_check():
        dataset_1.plot_residuals()


@requires_dependency("iminuit")
@requires_data()
def test_map_fit_one_energy_bin(sky_model, geom_image):
    energy_axis = geom_image.get_axis_by_name("energy")
    geom_etrue = geom_image.to_image().to_cube([energy_axis.copy(name="energy_true")])

    dataset = get_map_dataset(sky_model, geom_image, geom_etrue)
    sky_model.spectral_model.index.value = 3.0
    sky_model.spectral_model.index.frozen = True
    dataset.background_model.norm.value = 0.5

    dataset.counts = dataset.npred()

    # Move a bit away from the best-fit point, to make sure the optimiser runs
    sky_model.parameters["sigma"].value = 0.21
    dataset.background_model.parameters["norm"].frozen = True

    fit = Fit([dataset])
    result = fit.run()

    assert result.success

    npred = dataset.npred().data.sum()
    assert_allclose(npred, 16538.124036, rtol=1e-3)
    assert_allclose(result.total_stat, -34844.125047, rtol=1e-3)

    pars = result.parameters

    assert_allclose(pars["lon_0"].value, 0.2, rtol=1e-2)
    assert_allclose(pars["lon_0"].error, 0.001689, rtol=1e-2)

    assert_allclose(pars["sigma"].value, 0.2, rtol=1e-2)
    assert_allclose(pars["sigma"].error, 0.00092, rtol=1e-2)

    assert_allclose(pars["amplitude"].value, 1e-11, rtol=1e-2)
    assert_allclose(pars["amplitude"].error, 8.127593e-14, rtol=1e-2)


def test_create(geom, geom_etrue):
    # tests empty datasets created
    migra_axis = MapAxis(nodes=np.linspace(0.0, 3.0, 51), unit="", name="migra")
    rad_axis = MapAxis(nodes=np.linspace(0.0, 1.0, 51), unit="deg", name="theta")
    e_reco = MapAxis.from_edges(
        np.logspace(-1.0, 1.0, 3), name="energy", unit=u.TeV, interp="log"
    )
    e_true = MapAxis.from_edges(
        np.logspace(-1.0, 1.0, 4), name="energy_true", unit=u.TeV, interp="log"
    )
    geom = WcsGeom.create(binsz=0.02, width=(2, 2), axes=[e_reco])
    empty_dataset = MapDataset.create(
        geom=geom, energy_axis_true=e_true, migra_axis=migra_axis, rad_axis=rad_axis
    )

    assert empty_dataset.counts.data.shape == (2, 100, 100)

    assert empty_dataset.exposure.data.shape == (3, 100, 100)

    assert empty_dataset.psf.psf_map.data.shape == (3, 50, 10, 10)
    assert empty_dataset.psf.exposure_map.data.shape == (3, 1, 10, 10)

    assert empty_dataset.edisp.edisp_map.data.shape == (3, 50, 10, 10)
    assert empty_dataset.edisp.exposure_map.data.shape == (3, 1, 10, 10)
    assert_allclose(empty_dataset.edisp.edisp_map.data.sum(), 300)

    assert_allclose(empty_dataset.gti.time_delta, 0.0 * u.s)


@requires_data()
def test_stack(geom, geom_etrue):
    m = Map.from_geom(geom)
    m.quantity = 0.2 * np.ones(m.data.shape)
    background_model1 = BackgroundModel(
        m, name="dataset-1-bkg", datasets_names=["dataset-1"]
    )
    c_map1 = Map.from_geom(geom)
    c_map1.quantity = 0.3 * np.ones(c_map1.data.shape)
    mask1 = np.ones(m.data.shape, dtype=bool)
    mask1[0][0][0:10] = False
    mask1 = Map.from_geom(geom, data=mask1)

    dataset1 = MapDataset(
        counts=c_map1,
        models=[background_model1],
        exposure=get_exposure(geom_etrue),
        mask_safe=mask1,
        name="dataset-1",
    )

    c_map2 = Map.from_geom(geom)
    c_map2.quantity = 0.1 * np.ones(c_map2.data.shape)
    background_model2 = BackgroundModel(
        m, norm=0.5, name="dataset-2-bkg", datasets_names=["dataset-2"]
    )
    mask2 = np.ones(m.data.shape, dtype=bool)
    mask2[0][3] = False
    mask2 = Map.from_geom(geom, data=mask2)

    dataset2 = MapDataset(
        counts=c_map2,
        models=[background_model2],
        exposure=get_exposure(geom_etrue),
        mask_safe=mask2,
        name="dataset-2",
    )
    dataset1.stack(dataset2)
    assert_allclose(dataset1.counts.data.sum(), 7987)
    assert_allclose(dataset1.background_model.map.data.sum(), 5987)
    assert_allclose(dataset1.exposure.data, 2.0 * dataset2.exposure.data)
    assert_allclose(dataset1.mask_safe.data.sum(), 20000)


def to_cube(image):
    # introduce a fake enery axis for now
    axis = MapAxis.from_edges([1, 10] * u.TeV, name="energy")
    geom = image.geom.to_cube([axis])
    return WcsNDMap.from_geom(geom=geom, data=image.data)


@pytest.fixture
def images():
    """Load some `counts`, `counts_off`, `acceptance_on`, `acceptance_off" images"""
    filename = "$GAMMAPY_DATA/tests/unbundled/hess/survey/hess_survey_snippet.fits.gz"
    return {
        "counts": to_cube(WcsNDMap.read(filename, hdu="ON")),
        "counts_off": to_cube(WcsNDMap.read(filename, hdu="OFF")),
        "acceptance": to_cube(WcsNDMap.read(filename, hdu="ONEXPOSURE")),
        "acceptance_off": to_cube(WcsNDMap.read(filename, hdu="OFFEXPOSURE")),
        "exposure": to_cube(WcsNDMap.read(filename, hdu="EXPGAMMAMAP")),
        "background": to_cube(WcsNDMap.read(filename, hdu="BACKGROUND")),
    }


def get_map_dataset_onoff(images, **kwargs):
    """Returns a MapDatasetOnOff"""
    mask_geom = images["counts"].geom
    mask_data = np.ones(images["counts"].data.shape, dtype=bool)
    mask_safe = Map.from_geom(mask_geom, data=mask_data)

    return MapDatasetOnOff(
        counts=images["counts"],
        counts_off=images["counts_off"],
        acceptance=images["acceptance"],
        acceptance_off=images["acceptance_off"],
        exposure=images["exposure"],
        mask_safe=mask_safe,
        **kwargs
    )


@requires_data()
def test_map_dataset_on_off_fits_io(images, tmp_path):
    dataset = get_map_dataset_onoff(images)
    gti = GTI.create([0 * u.s], [1 * u.h], reference_time="2010-01-01T00:00:00")
    dataset.gti = gti

    hdulist = dataset.to_hdulist()
    actual = [hdu.name for hdu in hdulist]

    desired = [
        "PRIMARY",
        "COUNTS",
        "COUNTS_BANDS",
        "EXPOSURE",
        "EXPOSURE_BANDS",
        "MASK_SAFE",
        "MASK_SAFE_BANDS",
        "GTI",
        "COUNTS_OFF",
        "COUNTS_OFF_BANDS",
        "ACCEPTANCE",
        "ACCEPTANCE_BANDS",
        "ACCEPTANCE_OFF",
        "ACCEPTANCE_OFF_BANDS",
    ]

    assert actual == desired

    dataset.write(tmp_path / "test.fits")

    dataset_new = MapDatasetOnOff.read(tmp_path / "test.fits")
    assert dataset_new.models is None
    assert dataset_new.mask.dtype == bool

    assert_allclose(dataset.counts.data, dataset_new.counts.data)
    assert_allclose(dataset.counts_off.data, dataset_new.counts_off.data)
    assert_allclose(dataset.acceptance.data, dataset_new.acceptance.data)
    assert_allclose(dataset.acceptance_off.data, dataset_new.acceptance_off.data)
    assert_allclose(dataset.exposure.data, dataset_new.exposure.data)
    assert_allclose(dataset.mask_safe, dataset_new.mask_safe)

    assert dataset.counts.geom == dataset_new.counts.geom
    assert dataset.exposure.geom == dataset_new.exposure.geom

    assert_allclose(
        dataset.gti.time_sum.to_value("s"), dataset_new.gti.time_sum.to_value("s")
    )


def test_create_onoff(geom, geom_etrue):
    # tests empty datasets created

    migra_axis = MapAxis(nodes=np.linspace(0.0, 3.0, 51), unit="", name="migra")
    rad_axis = MapAxis(nodes=np.linspace(0.0, 1.0, 51), unit="deg", name="theta")
    energy_axis = geom.get_axis_by_name("energy").copy(name="energy_true")

    empty_dataset = MapDatasetOnOff.create(geom, energy_axis, migra_axis, rad_axis)

    assert_allclose(empty_dataset.counts.data.sum(), 0.0)
    assert_allclose(empty_dataset.counts_off.data.sum(), 0.0)
    assert_allclose(empty_dataset.acceptance.data.sum(), 0.0)
    assert_allclose(empty_dataset.acceptance_off.data.sum(), 0.0)

    assert empty_dataset.psf.psf_map.data.shape == (2, 50, 10, 10)
    assert empty_dataset.psf.exposure_map.data.shape == (2, 1, 10, 10)

    assert empty_dataset.edisp.edisp_map.data.shape == (2, 50, 10, 10)
    assert empty_dataset.edisp.exposure_map.data.shape == (2, 1, 10, 10)

    assert_allclose(empty_dataset.edisp.edisp_map.data.sum(), 200)

    assert_allclose(empty_dataset.gti.time_delta, 0.0 * u.s)


@requires_data()
def test_map_dataset_onoff_str(images):
    dataset = get_map_dataset_onoff(images)
    assert "MapDatasetOnOff" in str(dataset)


@requires_data()
def test_stack_onoff(images, geom_image):
    dataset = get_map_dataset_onoff(images)
    stacked = dataset.copy()

    stacked.stack(dataset)

    assert_allclose(stacked.counts.data.sum(), 2 * dataset.counts.data.sum())
    assert_allclose(stacked.counts_off.data.sum(), 2 * dataset.counts_off.data.sum())
    assert_allclose(
        stacked.acceptance.data.sum(), dataset.data_shape[1] * dataset.data_shape[2]
    )
    assert_allclose(
        np.nansum(stacked.acceptance_off.data),
        np.nansum(
            dataset.counts_off.data / (dataset.counts_off.data * dataset.alpha.data)
        ),
    )
    assert_allclose(stacked.exposure.data, 2.0 * dataset.exposure.data)


@pytest.mark.xfail
def test_stack_onoff_cutout(geom_image):
    # Test stacking of cutouts
    dataset = MapDatasetOnOff.create(geom_image)
    gti = GTI.create([0 * u.s], [1 * u.h], reference_time="2010-01-01T00:00:00")
    dataset.gti = gti

    geom_cutout = geom_image.cutout(position=geom_image.center_skydir, width=1 * u.deg)
    dataset_cutout = dataset.create(geom_cutout)

    dataset.stack(dataset_cutout)

    assert_allclose(dataset.counts.data.sum(), dataset_cutout.counts.data.sum())
    assert_allclose(dataset.counts_off.data.sum(), dataset_cutout.counts_off.data.sum())
    assert_allclose(dataset.alpha.data.sum(), dataset_cutout.alpha.data.sum())
    assert_allclose(dataset.exposure.data.sum(), dataset_cutout.exposure.data.sum())
    assert dataset_cutout.name != dataset.name


def test_datasets_io_no_model(tmpdir):
    axis = MapAxis.from_energy_bounds("1 TeV", "10 TeV", nbin=2)
    geom = WcsGeom.create(npix=(5, 5), axes=[axis])
    dataset_1 = MapDataset.create(geom, name="1")
    dataset_2 = MapDataset.create(geom, name="2")

    datasets = Datasets([dataset_1, dataset_2])

    datasets.write(path=tmpdir, prefix="test")

    filename_1 = tmpdir / "test_data_1.fits"
    assert filename_1.exists()

    filename_2 = tmpdir / "test_data_2.fits"
    assert filename_2.exists()


@requires_data()
def test_map_dataset_on_off_to_spectrum_dataset(images):
    e_reco = MapAxis.from_bounds(0.1, 10.0, 1, name="energy", unit=u.TeV, interp="log")
    new_images = dict()
    for key, image in images.items():
        new_images[key] = Map.from_geom(
            image.geom.to_cube([e_reco]), data=image.data[np.newaxis, :, :]
        )
    dataset = get_map_dataset_onoff(new_images)
    gti = GTI.create([0 * u.s], [1 * u.h], reference_time="2010-01-01T00:00:00")
    dataset.gti = gti

    on_region = CircleSkyRegion(
        center=dataset.counts.geom.center_skydir, radius=0.1 * u.deg
    )
    spectrum_dataset = dataset.to_spectrum_dataset(on_region)

    assert spectrum_dataset.counts.data[0] == 8
    assert spectrum_dataset.data_shape == (1, 1, 1)
    assert spectrum_dataset.counts_off.data[0] == 33914
    assert_allclose(spectrum_dataset.alpha.data[0], 0.0002143, atol=1e-7)

    excess_map = new_images["counts"] - new_images["background"]
    excess_true = excess_map.get_spectrum(on_region, np.sum).data[0]

    excess = spectrum_dataset.excess.data[0]
    assert_allclose(excess, excess_true, atol=1e-6)

    assert spectrum_dataset.name != dataset.name


@requires_data()
def test_map_dataset_on_off_cutout(images):
    dataset = get_map_dataset_onoff(images)
    gti = GTI.create([0 * u.s], [1 * u.h], reference_time="2010-01-01T00:00:00")
    dataset.gti = gti

    cutout_dataset = dataset.cutout(
        images["counts"].geom.center_skydir, ["1 deg", "1 deg"]
    )

    assert cutout_dataset.counts.data.shape == (1, 50, 50)
    assert cutout_dataset.counts_off.data.shape == (1, 50, 50)
    assert cutout_dataset.acceptance.data.shape == (1, 50, 50)
    assert cutout_dataset.acceptance_off.data.shape == (1, 50, 50)
    assert cutout_dataset.background_model is None
    assert cutout_dataset.name != dataset.name


@requires_data()
def test_map_dataset_on_off_to_image():
    axis = MapAxis.from_energy_bounds(1, 10, 2, unit="TeV")
    geom = WcsGeom.create(npix=(10, 10), binsz=0.05, axes=[axis])

    counts = Map.from_geom(geom, data=np.ones((2, 10, 10)))
    counts_off = Map.from_geom(geom, data=np.ones((2, 10, 10)))
    acceptance = Map.from_geom(geom, data=np.ones((2, 10, 10)))
    acceptance_off = Map.from_geom(geom, data=np.ones((2, 10, 10)))
    acceptance_off *= 2

    dataset = MapDatasetOnOff(
        counts=counts,
        counts_off=counts_off,
        acceptance=acceptance,
        acceptance_off=acceptance_off,
    )
    image_dataset = dataset.to_image()

    assert image_dataset.counts.data.shape == (1, 10, 10)
    assert image_dataset.acceptance_off.data.shape == (1, 10, 10)
    assert_allclose(image_dataset.acceptance, 2)
    assert_allclose(image_dataset.acceptance_off, 4)
    assert_allclose(image_dataset.counts_off, 2)
    assert image_dataset.name != dataset.name

    # Try with a safe_mask
    mask_safe = Map.from_geom(geom, data=np.ones((2, 10, 10), dtype="bool"))
    mask_safe.data[0] = 0
    dataset.mask_safe = mask_safe
    image_dataset = dataset.to_image()

    assert_allclose(image_dataset.acceptance, 1)
    assert_allclose(image_dataset.acceptance_off, 2)
    assert_allclose(image_dataset.counts_off, 1)


def test_map_dataset_geom(geom, sky_model):
    e_true = MapAxis.from_energy_bounds("1 TeV", "10 TeV", nbin=5, name="energy_true")
    dataset = MapDataset.create(geom, energy_axis_true=e_true)
    dataset.counts = None
    dataset.background_model = None

    dataset.models = sky_model

    npred = dataset.npred()
    assert npred.geom == geom

    dataset.mask_safe = None

    with pytest.raises(ValueError):
        dataset._geom


@requires_data()
def test_names(geom, geom_etrue, sky_model):

    m = Map.from_geom(geom)
    m.quantity = 0.2 * np.ones(m.data.shape)
    background_model1 = BackgroundModel(m, name="bkg1", datasets_names=["test"])
    assert background_model1.name == "bkg1"

    c_map1 = Map.from_geom(geom)
    c_map1.quantity = 0.3 * np.ones(c_map1.data.shape)

    model1 = sky_model.copy()
    assert model1.name != sky_model.name
    model1 = sky_model.copy(name="model1")
    assert model1.name == "model1"
    model2 = sky_model.copy(name="model2")

    dataset1 = MapDataset(
        counts=c_map1,
        models=Models([model1, model2, background_model1]),
        exposure=get_exposure(geom_etrue),
        name="test",
    )

    dataset2 = dataset1.copy()
    assert dataset2.name != dataset1.name
    assert dataset2.background_model
    dataset2 = dataset1.copy(name="dataset2")
    assert dataset2.name == "dataset2"
    assert dataset2.background_model.name == "bkg1"
    assert dataset1.background_model is not dataset2.background_model
    assert dataset1.models.names == dataset2.models.names
    assert dataset1.models is not dataset2.models


def test_stack_dataset_dataset_on_off():
    axis = MapAxis.from_edges([1, 10] * u.TeV, name="energy")
    geom = WcsGeom.create(width=1, axes=[axis])

    gti = GTI.create([0 * u.s], [1 * u.h])

    dataset = MapDataset.create(geom, gti=gti)
    dataset_on_off = MapDatasetOnOff.create(geom, gti=gti)
    dataset_on_off.mask_safe.data += True

    dataset_on_off.acceptance_off += 5
    dataset_on_off.acceptance += 1
    dataset_on_off.counts_off += 1
    dataset.stack(dataset_on_off)

    assert_allclose(dataset.background_model.map.data, 0.2)
