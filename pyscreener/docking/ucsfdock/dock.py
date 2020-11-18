from functools import partial
import os
from pathlib import Path
import subprocess as sp
import sys
import timeit
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from tqdm import tqdm

from pyscreener.docking import Screener
from pyscreener.docking.ucsfdock import _docking as ucsfdock_docking
from pyscreener.docking.ucsfdock import _preparation as ucsfdock_prep

DOCK6 = Path(os.environ['DOCK6'])
DOCK6_PARAMS = DOCK6 / 'parameters'
VDW_DEFN_FILE = DOCK6_PARAMS / 'vdw_AMBER_parm99.defn'
FLEX_DEFN_FILE = DOCK6_PARAMS / 'flex.defn'
FLEX_DRIVE_FILE = DOCK6_PARAMS / 'flex_drive.tbl'

DOCK6 = str(DOCK6 / 'bin' / 'dock6')

class DOCK(Screener):
    """A wrapper around the DOCK6 software suite to performing computaional
    DOCKing via python calls.

    NOTE: there are several steps in the receptor preparation process, each
          with their own set of options. Two important steps are:
          (1) selecting spheres to represent the binding site in the dockign 
              simulations
          (2) calculating the grid box for the scoring function
          Both of these steps can rely on some prior information about the
          binding site or do their best to calculate one.  In (1), if both a 
          docked ligand is provided and center is specified, the docked ligand 
          will take precedence. Either of these will take precedence over the 
          use_largest flag. In (2), if a docking box center is specified, it 
          will be used only if enclose_spheres is set to False (default = True.)
    """
    def __init__(self, receptors: Optional[List[str]] = None,
                 pdbids: Optional[List[str]] = None,
                 center: Optional[Tuple[float, float, float]] = None,
                 size: Tuple[float, float, float] = (20., 20., 20.), 
                 docked_ligand_file: Optional[str] = None,
                 use_largest: bool = False, buffer: float = 10.,
                 enclose_spheres: bool = True,
                 repeats: int = 1, score_mode: str = 'best',
                 receptor_score_mode: str = 'best', 
                 ensemble_score_mode: str = 'best',
                 distributed: bool = False, num_workers: int = -1,
                 path: str = '.', verbose: int = 0, **kwargs):
        if docked_ligand_file is None and center is None and not use_largest:
            print('WARNING: Args "docked_ligand_file" and "center" were both ',
                  'None and use_largest was False. Overriding to True.')
            use_largest = True
        if center is None and not enclose_spheres:
            print('WARNING: Arg "center" was None but arg "enclose_spheres"',
                  'was False. Overriding to True.')
            enclose_spheres = True
        
        self.center = center
        self.size = size
        self.docked_ligand_file = docked_ligand_file
        self.use_largest = use_largest
        self.buffer = buffer
        self.enclose_spheres = enclose_spheres
        # self.receptors = receptors
        self.repeats = repeats

        super().__init__(receptors=receptors, pdbids=pdbids,
                         score_mode=score_mode, 
                         receptor_score_mode=receptor_score_mode,
                         ensemble_score_mode=ensemble_score_mode,
                         distributed=distributed, num_workers=num_workers,
                         path=path, verbose=verbose, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.dock(*args, **kwargs)

    # @property
    # def receptors(self):
    #     return self.__receptors

    # @receptors.setter
    # def receptors(self, receptors):
    #     receptors = [self.prepare_receptor(receptor) for receptor in receptors]
    #     receptors = [receptor for receptor in receptors if receptor is not None]
    #     if len(receptors) == 0:
    #         raise RuntimeError('Preparation failed for all receptors!')
    #     self.__receptors = receptors
        
    def prepare_receptor(self, receptor: str) -> Optional[Tuple[str, str]]:
        return ucsfdock_prep.prepare_receptor(
            receptor, center=self.center, size=self.size,
            docked_ligand_file=self.docked_ligand_file,
            use_largest=self.use_largest, buffer=self.buffer,
            enclose_spheres=self.enclose_spheres, path=self.in_path
        )

    @staticmethod
    def prepare_from_smi(smi: str, name: str = 'ligand',
                         path: str = '.') -> Tuple[str, str]:
        return ucsfdock_prep.prepare_from_smi(smi, name, path)

    @staticmethod
    def prepare_from_file(filepath: str, use_3d: bool = False,
                          name: Optional[str] = None, path: str = '.'):
        return ucsfdock_prep.prepare_from_file(filepath, use_3d, name, path)

    def run_docking(self, ligands: Sequence[Tuple[str, str]]
                   ) -> List[List[List[Dict]]]:
        dock_ligand = partial(
            DOCK.dock_ligand, receptors=self.receptors,
            in_path=self.in_path, out_path=self.out_path,
            repeats=self.repeats, score_mode=self.score_mode
        )
        CHUNKSIZE = 1
        with self.Pool(self.distributed, self.num_workers) as pool:
            ligs_recs_reps = pool.map(dock_ligand, ligands, 
                                      chunksize=CHUNKSIZE)
            ligs_recs_reps = list(tqdm(ligs_recs_reps, total=len(ligands),
                                       desc='Docking', unit='ligand'))

        return ligs_recs_reps

    @staticmethod
    def dock_ligand(ligand: Tuple[str, str], receptors: List[Tuple[str, str]],
                    in_path: Union[str, os.PathLike] = 'inputs',
                    out_path: Union[str, os.PathLike] = 'outputs',
                    repeats: int = 1, score_mode: str = 'best'
                    ) -> List[List[Dict]]:
        """Dock this ligand into the ensemble of receptors

        Parameters
        ----------
        ligand : Tuple[str, str]
            a tuple containing the ligand's SMILES string and its prepared
            .mol2 file that will be docked against each receptor
        receptors : List[Tuple[str, str]]
            a list of tuples containing the sphere file and grid file prefix
            corresponding to each receptor in the ensemble.
        in_path : Union[str, os.PathLike] (Default = 'inputs')
            the path under which to write the input files
        out_path : Union[str, os.PathLike] (Default = 'outputs')
            the path under which to write the output files
        repeats : int (Default = 1)
            the number of times each docking run should be repeated
        score_mode : str (Default = 'best')
            The method used to calculate the docking score from the outfile file. See also Screener.calc_score for more details
        
        Returns
        -------
        ensemble_rowss : List[List[Dict]]
            an MxO list of dictionaries where each dictionary is a record of an 
            individual docking run and:
            - M is the number of receptors each ligand is docked against
            - O is the number of times each docking run is repeated.
            Each dictionary contains the following keys:
            - smiles: the ligand's SMILES string
            - name: the name of the ligand
            - in: the filename of the input ligand file
            - out: the filename of the output docked ligand file
            - log: the filename of the output log file
            - score: the ligand's docking score
        """
        if repeats <= 0:
            raise ValueError(f'Repeats must be greater than 0! ({repeats})')

        smi, lig_mol2 = ligand

        ensemble_rowss = []
        for sph_file, grid_prefix in receptors:
            repeat_rows = []
            for repeat in range(repeats):
                name = f'{Path(sph_file).stem}_{Path(lig_mol2).stem}_{repeat}'

                infile, outfile_prefix = DOCK.prepare_input_file(
                    lig_mol2, sph_file, grid_prefix, name, in_path, out_path
                )

                out = Path(f'{outfile_prefix}_scored.mol2')
                log = Path(outfile_prefix).parent / f'{name}.out'
                argv = [DOCK6, '-i', infile, '-o', log]

                ret = sp.run(argv, stdout=sp.PIPE, stderr=sp.PIPE)
                try:
                    ret.check_returncode()
                except sp.SubprocessError:
                    print(f'ERROR: docking failed. argv: {argv}',
                          file=sys.stderr)
                    print(f'Message: {ret.stderr.decode("utf-8")}',
                          file=sys.stderr)
                    # print('Skipping...', file=sys.stderr, flush=True)
                    # continue

                repeat_rows.append({
                    'smiles': smi,
                    'name': name,
                    'in': infile,
                    'log': log,
                    'out': out,
                    'score': DOCK.parse_out_file(out, score_mode)
                })

            if repeat_rows:
                ensemble_rowss.append(repeat_rows)

        return ensemble_rowss

    @staticmethod
    def parse_out_file(outfile: Union[str, os.PathLike],
                       score_mode: str = 'best') -> Optional[float]:
        """Parse the out file generated from a run of DOCK to calculate an
        overall ligand score

        Parameters
        ----------
        outfile : Union[str, PathLike]
            the filename of a scored outfile file generated by DOCK6 or a 
            PathLike object pointing to that file
        score_mode : str (Default = 'best')
            The method used to calculate the docking score from the outfile file. See also Screener.calc_score for more details

        Returns
        -------
        score : Optional[float]
            the parsed score given the input scoring mode or None if the log
            file was unparsable for whatever reason
        """
        scores = []
        try:
            with open(outfile) as fid:
                for line in fid:
                    if 'Grid_Score:' in line:
                        try:
                            scores.append(float(line.split()[2]))
                        except:
                            continue
            if len(scores) == 0:
                score = None
            else:
                score = Screener.calc_score(scores, score_mode)
        except OSError:
            score = None
        
        return score
    
    @staticmethod
    def parse_ligand_results(ligand_results: List[List[Dict]],
                             score_mode: str = 'best'):
        for receptor_results in ligand_results:
            for repeat_result in receptor_results:
                score = DOCK.parse_out_file(repeat_result['out'], score_mode)

                repeat_result['score'] = score
                # p_in = repeat_result['in']
                # repeat_result['in'] = Path(p_in.parent.name) / p_in.name
                # p_out = repeat_result['out']
                # repeat_result['out'] = Path(p_out.parent.name) / p_out.name
                # p_log = repeat_result['log']
                # repeat_result['log'] = Path(p_log.parent.name) / p_log.name
        return ligand_results
    
    @staticmethod
    def prepare_input_file(ligand_file: str, sph_file: str, grid_prefix: str,
                        name: Optional[str] = None,
                        in_path: Union[str, os.PathLike] = 'inputs',
                        out_path: Union[str, os.PathLike] = 'outputs'
                        ) -> Tuple[str, str]:
        """Prepare the input file with which to run DOCK

        Parameters
        ----------
        ligand_file : str
            the input .mol2 corresponding to the ligand that will be docked
        sph_file : str
            the .sph file containing the DOCK spheres of the receptor
        grid_prefix : str
            the prefix of the prepared grid files (as was passed to 
            the grid program)
        name : Optional[str] (Default = None)
            the name to use for the input file and output file
        in_path : Union[str, os.PathLike] (Default = 'inputs')
            the path under which to write the input files
            both the input file and output
        out_path : Union[str, os.PathLike] (Default = 'outputs')
            the path under which to write the output files

        Returns
        -------
        infile: str
            the name of the input file
        outfile_prefix: str
            the prefix of the outfile name. DOCK will automatically name 
            outfiles as <outfile_prefix>_scored.mol2
        """
        in_path = Path(in_path)
        if not in_path.is_dir():
            in_path.mkdir(parents=True)
        out_path = Path(out_path)
        if not out_path.is_dir():
            out_path.mkdir(parents=True)

        name = name or f'{Path(sph_file).stem}_{Path(ligand_file).stem}'
        infile = in_path / f'{name}.in'
        outfile_prefix = out_path / name

        with open(infile, 'w') as fid:
            fid.write('conformer_search_type flex\n')
            fid.write('write_fragment_libraries no\n')
            fid.write('user_specified_anchor no\n')
            fid.write('limit_max_anchors no\n')
            fid.write('min_anchor_size 5\n')

            fid.write('pruning_use_clustering yes\n')
            fid.write('pruning_max_orients 100\n')
            fid.write('pruning_clustering_cutoff 100\n')
            fid.write('pruning_conformer_score_cutoff 100.0\n')
            fid.write('pruning_conformer_score_scaling_factor 1.0\n')

            fid.write('use_clash_overlap no\n')
            fid.write('write_growth_tree no\n')
            fid.write('use_internal_energy yes\n')
            fid.write('internal_energy_rep_exp 12\n')
            fid.write('internal_energy_cutoff 100.0\n')

            fid.write(f'ligand_atom_file {ligand_file}\n')
            fid.write('limit_max_ligands no\n')
            fid.write('skip_molecule no\n')
            fid.write('read_mol_solvation no\n')
            fid.write('calculate_rmsd no\n')
            fid.write('use_rmsd_reference_mol no\n')
            fid.write('use_database_filter no\n')
            fid.write('orient_ligand yes\n')
            fid.write('automated_matching yes\n')
            fid.write(f'receptor_site_file {sph_file}\n')
            fid.write('max_orientations 1000\n')
            fid.write('critical_points no\n')
            fid.write('chemical_matching no\n')
            fid.write('use_ligand_spheres no\n')
            fid.write('bump_filter no\n')
            fid.write('score_molecules yes\n')

            fid.write('contact_score_primary no\n')
            fid.write('contact_score_secondary no\n')

            fid.write('grid_score_primary yes\n')
            fid.write('grid_score_secondary no\n')
            fid.write('grid_score_rep_rad_scale 1\n')
            fid.write('grid_score_vdw_scale 1\n')
            fid.write('grid_score_es_scale 1\n')
            fid.write(f'grid_score_grid_prefix {grid_prefix}\n')

            fid.write('multigrid_score_secondary no\n')
            fid.write('dock3.5_score_secondary no\n')
            fid.write('continuous_score_secondary no\n')
            fid.write('footprint_similarity_score_secondary no\n')
            fid.write('pharmacophore_score_secondary no\n')
            fid.write('descriptor_score_secondary no\n')
            fid.write('gbsa_zou_score_secondary no\n')
            fid.write('gbsa_hawkins_score_secondary no\n')
            fid.write('SASA_score_secondary no\n')
            fid.write('amber_score_secondary no\n')

            fid.write('minimize_ligand yes\n')
            fid.write('minimize_anchor yes\n')
            fid.write('minimize_flexible_growth yes\n')
            fid.write('use_advanced_simplex_parameters no\n')

            fid.write('simplex_max_cycles 1\n')
            fid.write('simplex_score_converge 0.1\n')
            fid.write('simplex_cycle_converge 1.0\n')
            fid.write('simplex_trans_step 1.0\n')
            fid.write('simplex_rot_step 0.1\n')
            fid.write('simplex_tors_step 10.0\n')
            fid.write('simplex_anchor_max_iterations 500\n')
            fid.write('simplex_grow_max_iterations 500\n')
            fid.write('simplex_grow_tors_premin_iterations 0\n')
            fid.write('simplex_random_seed 0\n')
            fid.write('simplex_restraint_min no\n')

            fid.write('atom_model all\n')
            fid.write(f'vdw_defn_file {VDW_DEFN_FILE}\n')
            fid.write(f'flex_defn_file {FLEX_DEFN_FILE}\n')
            fid.write(f'flex_drive_file {FLEX_DRIVE_FILE}\n')

            fid.write(f'ligand_outfile_prefix {outfile_prefix}\n')
            fid.write('write_orientations no\n')
            fid.write('num_scored_conformers 5\n')
            fid.write('write_conformations no\n')
            fid.write('rank_ligands no\n')
        
        return infile, outfile_prefix
