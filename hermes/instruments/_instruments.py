"""Instrument classes."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import os
import numpy as np
import pandas as pd
from pydantic.dataclasses import dataclass as typesafedataclass
import time

from hermes.utils import _check_attr

from . import PySpecClient
from pyspec.client.SpecConnection import SpecConnection

# conditional import of pyspec to support testing on Windows with simulation mode
try:
    import pyspec
    from pyspec.client import spec
except ModuleNotFoundError:
    print("pyspec not found; CHESSQM2Beamline only supports `simulation=True`")
    pyspec = None

### import integration code
from .QM2_pyfai_integrate import data_reduction, integrate_images
poni_path = "/nfs/chess/sw/hermes/hermes/instruments/lab6_17keV.poni"

@dataclass
class Instrument:
    """Base level class for communicating with instruments."""


@dataclass
class Diffractometer(Instrument):
    """Class for diffractometer instruments"""


config1 = {"allow_arbitrary_types": True}


class _Config:  # pylint: disable=too-few-public-methods
    arbitrary_types_allowed = True


@typesafedataclass(config=_Config)
class PowderDiffractometer(Diffractometer):
    """Class for Powder (aka 1D) diffractometer instruments.
    Typically expect the sample to be a combinatorial wafer (each location has a known, pre-determined composition),
    but ignore the composition information for general samples."""

    # Location for example data used in simulation mode:
    wafer_directory: Path
    # Location for wafer coordinates file (tab delimited .txt)
    wafer_coords_file: Path  # relative to wafer_directory
    # Location for wafer composition file (tab delimited .txt)
    wafer_composition_file: Optional[Any] = field(init=False, default=None)  # relative to wafer_directory
    # Location for XRD measurements file (tab delimited .txt)
    wafer_xrd_file: Optional[Any] = field(init=False, default=None)
    diffraction_space_name: Optional[str] = "Q-space"
    diffraction_space_bins: Optional[int] = None 

    xy_locations: Optional[pd.DataFrame] = field(init=False, default=None)
    compositions: Optional[pd.DataFrame] = field(init=False, default=None)
    xrd_measurements: Optional[pd.DataFrame] = field(init=False, default=None)

    def load_wafer_data(self):
        """Load simulated data."""
        self.xy_locations = pd.read_table(
            self.wafer_directory.joinpath(self.wafer_coords_file)
        )
        if self.wafer_composition_file is not None:
            self.compositions = pd.read_table(
                self.wafer_directory.joinpath(self.wafer_composition_file)
            )
        self.xrd_measurements = None
        if self.diffraction_space_bins is not None:
            self.xrd_measurements = np.array([]).reshape(-1, self.diffraction_space_bins)

    def load_sim_data(self):
        self.xrd_measurements = pd.read_table(
            self.wafer_directory.joinpath(self.wafer_xrd_file)
        )

    def simulated_move_and_measure(self, compositions_locations):
        """Move (in composition-space) to new locations
        and return the XRD measurements."""

        # # If the data for simulation mode hasn't been loaded, load it.
        # if not self.xy_locations:
        #     self.load_sim_data()

        _check_attr(self, "compositions")
        _check_attr(self, "xrd_measurements")
        indexes = []
        for comp in compositions_locations:
            index = self.compositions[self.compositions.to_numpy() == comp].index[0]
            indexes.append(index)

        measurements = self.xrd_measurements.iloc[indexes, :].to_numpy()
        return measurements

    @property
    def sim_wafer_coords(self):
        """Get all of the possible coordinates of the sample"""
        _check_attr(self, "xy_locations")
        return self.xy_locations.to_numpy()

    @property
    def sim_composition_domain(self):
        """Get the entire domain in composition space."""
        _check_attr(self, "compositions")
        components = self.compositions.columns.to_list()
        fractions = self.compositions.to_numpy()
        return components, fractions

    @property
    def sim_two_theta_space(self):
        """Get the 2Theta values of the XRD measurements in degrees"""
        _check_attr(self, "xrd_measurements")
        two_theta = self.xrd_measurements.columns.to_numpy().astype(float)
        return two_theta

    @property
    def compositions_2d(self):
        """Converting the compostions from the 3D simplex to a 2D triangle
        NOTE: the triangle is smaller than the simplex by a factor of sqrt(2)."""
        _check_attr(self, "compositions")
        # In 3D space
        A_3d = np.array([1, 0, 0])
        B_3d = np.array([0, 1, 0])
        C_3d = np.array([0, 0, 1])

        # In 2D space
        A_2d = np.array([0, 0])  # A at the origin
        B_2d = np.array([1, 0])  # B at the x-axis = 1 point
        C_2d = np.array(
            [0.5, 0.5 * np.sqrt(3)]
        )  # C at the top of an equilateral triangle with the base along x of length 1.

        points = self.compositions.to_numpy()  # Read in the 3D compostions
        # Multiply 2D coordinates with the compositions for each component
        points_A = points[:, 0].reshape(-1, 1) * A_2d.reshape(1, -1)
        points_B = points[:, 1].reshape(-1, 1) * B_2d.reshape(1, -1)
        points_C = points[:, 2].reshape(-1, 1) * C_2d.reshape(1, -1)
        # Sum the coordinates for each component
        points_2d = points_A + points_B + points_C

        return points_2d

    # def compositions_2d_to_index(self, locations_2d):


@typesafedataclass(config=_Config)
class CHESSQM2Beamline(PowderDiffractometer):
    """Class for the QM2 diffractometer at CHESS"""

    simulation: bool = False
    specname: Optional[str] = None



    spec_data_dir = "/nfs/chess/id4b/2023-2/sarker-3729-a/raw6M/"
    spec_det_dir="/mnt/currentdaq/sarker-3729-a/raw6M/"
 
    sample_name = "CuV_multi_120522"
    


    # high level folder where all integrated histograms are stored
    reduction_dir = "/nfs/chess/id4baux/2023-2/sarker-3729-a/hermes_061623/"
    
    reduced_sample_dir = reduction_dir + sample_name


    def __post_init_post_parse__(self):
        # load xy coordinates and compositions for discrete library sample
        self.load_wafer_data()

        if self.simulation:
            # load simulated XRD measurements
            self.load_sim_data()

        elif pyspec == None:
            raise (
                ModuleNotFoundError(
                    "CHESSQM2Beamline requires pyspec if simulation==False"
                )
            )

        else:
            self.specsession = PySpecClient.PySpecClient("id4b.classe.cornell.edu", "fourc")
            self.specsession.conn = f"{self.specsession.address}:{self.specsession.port}"
            self.specsession.spec = SpecConnection(self.specsession.conn)
            self.specsession.run_cmd("p 'hello'")
            #self.specsession = spec(self.specname)
            self.specsession.run_cmd(f"cd {self.spec_data_dir}")
            dir_exists = os.path.exists(f"{self.spec_data_dir+self.sample_name}")
            if dir_exists == False:
            	self.specsession.run_cmd(f"u mkdir {self.sample_name}")
            self.specsession.run_cmd(f"cd {self.sample_name}")
            red_dir_exists = os.path.exists(f"{self.reduced_sample_dir}")
            if red_dir_exists ==False:
                os.mkdir(self.reduced_sample_dir)

    def load_wafer_file(self):
        """Load the wafer file."""
        self.xy_locations = pd.read_table(
            self.wafer_directory.joinpath(self.wafer_coords_file)
        )
        self.compositions = pd.read_table(
            self.wafer_directory.joinpath(self.wafer_composition_file)
        )
        self.xrd_measurements = pd.read_table(
            self.wafer_directory.joinpath(self.wafer_xrd_file)
        )

    def move_and_measure(self, indexes) -> np.ndarray:
        """Move (in composition-space) to new locations
        and return the XRD measurements."""

        # print(f"{compositions_locations=}")

        if self.simulation:
            measurements = self.simulated_move_and_measure(compositions_locations)

        else:
            # print(f"{pyspec.__version__=}")

            # Convert composition to wafer coordinates
            #indexes = []
            #for comp in compositions_locations:
            #    index = self.compositions[self.compositions.to_numpy() == comp].index[0]
            #    indexes.append(index)

            #wafer_coords = self.xy_locations.iloc[indexes, :].to_numpy()

            

            # For each location:
            measurements = np.array([]).reshape(-1, self.diffraction_space_bins)
            for idx, row in self.xy_locations.loc[indexes].iterrows():
                # configure new datafile
                site_name = f"{self.sample_name}_{idx}"
                site_dir = self.spec_data_dir  + self.sample_name + "/" + f"{site_name}"
                self.specsession.run_cmd(f"u mkdir {site_dir}")
                self.specsession.run_cmd(f"cd {site_dir}")
                self.specsession.run_cmd(f"newfile {site_name}")
                site_dir_det = self.spec_det_dir + self.sample_name + "/" + f"{site_name}"
                self.specsession.run_cmd(f"pil_setdir {site_dir_det}")

                # Move to wafer coordinates
                # print(idx, f"{row.x=}, {row.y=}")
                self.specsession.run_cmd(f"umv waferx {row.x} wafery {row.y}")
                self.specsession.run_cmd("opens")
                self.specsession.run_cmd("pil_on")
                mysamplepath = "pyspec/"
                self.specsession.run_cmd(f"u mkdir {mysamplepath}")
                mysample = self.sample_name
                full_sample_path = mysamplepath+mysample
                self.specsession.run_cmd(f"u mkdir -p {full_sample_path}")
                time.sleep(5)
                pilrampath = "/mnt/currentdaq/sarker-3729-a/" + mysamplepath + mysample
                self.specsession.run_cmd(f"pil_setdir {pilrampath}")
#                self.specsession.run_cmd("pil_fly_on")
#                self.specsession.run_cmd("pil_settrig 'Mult. Trigger'")

                self.specsession.run_cmd("flyscan th 4 26 440 1")
                self.specsession.run_cmd("pil_off")
                self.specsession.run_cmd("closes")

                
                
		# integrate the images
                #image_file = site_dir + "/" + site_name + "_001" + "/" + site_name + "_PIL10_001_000.cbf"
                image_file = "/nfs/chess/id4b/2023-2/sarker-3729-a/" + mysamplepath + mysample
                export_path = self.reduced_sample_dir + '/' + site_name + '/'  
                os.mkdir(self.reduced_sample_dir + '/' + site_name)
                label = site_name + '_001' + "/" + site_name + '_PIL10_001_000'
                label2 = site_name + '_PIL10_001_000'

                # sleep to let the image file save
                time.sleep(5)       

                try:
                    measurement = data_reduction(image_file + '/' + label + '.cbf', poni_path, export_path,label = label2,thbin = self.diffraction_space_bins)
                except:
                    print('~~~~~~~~~~~~~~~~START WAIT~~~~~~~~~~~~~~~~~~~~~~')
                    time.sleep(30)
                    measurement = data_reduction(image_file + '/' + label + '.cbf', poni_path, export_path,label = label2,thbin = self.diffraction_space_bins)
                measurements = np.concatenate((measurements, np.array(measurement).reshape(1,-1)), axis = 0)

                # TODO: map sample reference frame to  motor coordinates
                # phi = self.specsession.get_motor("phi")
                # phi.get_position()
                # phi.mvr(12.0) #

                # Measure - collect XRD
                # insert spec subroutine for data collection here

                # Reduce - integrate
                # datafile = sess.get("DATAFILE")
                # data = read(datafile)
                # y = integrate(data)
                # measurements.append(y)

            # Concatenate measurements - return numpy array
            # measurements = np.vstack(measurements)

        return measurements

    @property
    def wafer_coords(self):
        """Get all of the possible coordinates of the sample"""
        _check_attr(self, "xy_locations")
        return self.xy_locations.to_numpy()

    @property
    def composition_domain(self):
        """Get the entire domain in composition space."""
        # _check_attr(self, "compositions")
        if self.compositions is not None:
            components = self.compositions.columns.to_list()
            fractions = self.compositions.to_numpy()
            return components, fractions
        else:
            return None

    @property
    def composition_domain_2d(self):
        """Converting the compostions from the 3D simplex to a 2D triangle
        NOTE: the triangle is smaller than the simplex by a factor of sqrt(2)."""

        _check_attr(self, "compositions")
        # In 3D space
        A_3d = np.array([1, 0, 0])
        B_3d = np.array([0, 1, 0])
        C_3d = np.array([0, 0, 1])

        # In 2D space
        A_2d = np.array([0, 0])  # A at the origin
        B_2d = np.array([1, 0])  # B at the x-axis = 1 point
        C_2d = np.array(
            [0.5, 0.5 * np.sqrt(3)]
        )  # C at the top of an equilateral triangle with the base along x of length 1.

        points = self.composition_domain[1]  # Read in the 3D compostions
        # Multiply 2D coordinates with the compositions for each component
        points_A = points[:, 0].reshape(-1, 1) * A_2d.reshape(1, -1)
        points_B = points[:, 1].reshape(-1, 1) * B_2d.reshape(1, -1)
        points_C = points[:, 2].reshape(-1, 1) * C_2d.reshape(1, -1)
        # Sum the coordinates for each component
        points_2d = points_A + points_B + points_C

        return points_2d

    @property
    def diffraction_space(self):
        """Get the 2Theta values of the XRD measurements in degrees"""
        _check_attr(self, "xrd_measurements")
        diff_space = self.xrd_measurements.columns.to_numpy().astype(float)
        return diff_space

    # @property
    # def q_space(self):
    #     lambda = self.wavelength
    #     #some conversion here

    #     return q

    # @dataclass
    # class BrukerD8(PowderDiffractometer):
    #     #something
