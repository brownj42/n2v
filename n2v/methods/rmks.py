"""
mrks.py

Functions associated with mrks inversion
"""

import numpy as np
from opt_einsum import contract
import psi4
import time

class MRKS():
    """
    Wavefunction to KS_potential method based on
    [1] [PRL 115, 083001 (2015)],
    [2] [J. Chem. Phys. 146, 084103 (2017)].

    The XC potential is calculated on the grid
    instead of on the potential basis set.
    Whereas, the guide potential is still used and
    plays the role of v_hartree.
    And because of this, the gird for vxc for output
    has to be specified beforehand.
    
    For CIWavefunction as input, make sure to turn on
    option opdm and tpdm:
        psi4.set_options({
            "opdm": True,
            "tpdm": True,
            'REFERENCE': "RHF"
            })

    Attributes:
    -----------
    Vpot: psi4.core.VBase
        V_potential that contains the info of DFT spherical grid.
    npoint_DFT: int
        number of points for DFT spherical grid.
    vxc_hole_WF: np.ndarray
        vxc_hole_WF on spherical grid. This is stored
        because the calculation of this takes most time.
    """
    vxc_hole_WF = None

    def _vxc_hole_quadrature(self, grid_info=None, atol = 1e-4):
        """
        Calculating v_XC^hole in [1] (15) using quadrature
        integral on the default DFT spherical grid.
        """
        if self.vxc_hole_WF is not None and grid_info is None:
            return self.vxc_hole_WF


        if self.wfn.name() == "CIWavefunction":
            Tau_ijkl = self.wfn.get_tpdm("SUM", True).np
            D2 = self.wfn.get_opdm(-1, -1, "SUM", True).np
            C = self.wfn.Ca()
            Tau_ijkl = contract("pqrs,ip,jq,ur,vs->ijuv", Tau_ijkl, C, C, C, C)
            D2 = C.np @ D2 @ C.np.T
        else:
            D2a = self.wfn.Da().np
            D2b = self.wfn.Db().np
            D2 = D2a + D2b

        if grid_info is None:
            vxchole = np.zeros(self.npoints_DFT)
            nblocks = self.Vpot.nblocks()
    
            points_func = self.Vpot.properties()[0]
            points_func.set_deriv(0)
    
            blocks = None
        else:
            blocks, npoints, points_func = grid_info
            vxchole = np.zeros(npoints)
            nblocks = len(blocks)
            points_func.set_deriv(0)

        # First loop over the outer set of blocks
        num_block_ten_percent = int(nblocks / 10)
        w1_old = 0
        print("vxchole quadrature double integral starts (%i points): " % (self.npoints_DFT), end="")
        start_time = time.time()
        for l_block in range(nblocks):
            # Print out progress
            if num_block_ten_percent != 0 and l_block % num_block_ten_percent == 0:
                print(".", end="")
    
            # Obtain general grid information
            if blocks is None:
                l_grid = self.Vpot.get_block(l_block)
            else:
                l_grid = blocks[l_block]
    
            l_x = np.array(l_grid.x())
            l_y = np.array(l_grid.y())
            l_z = np.array(l_grid.z())
            l_npoints = l_x.shape[0]
    
            points_func.compute_points(l_grid)
    
            l_lpos = np.array(l_grid.functions_local_to_global())
            l_phi = np.array(points_func.basis_values()["PHI"])[:l_npoints, :l_lpos.shape[0]]
    
            # if restricted:
            lD1 = D2[(l_lpos[:, None], l_lpos)]
            rho1 = contract('pm,mn,pn->p', l_phi, lD1, l_phi)
            rho1inv = (1 / rho1)[:, None]

            dvp_l = np.zeros_like(l_x)

            # if not restricted:
            #     dvp_l_b = np.zeros_like(l_x)
    
            # Loop over the inner set of blocks
            for r_block in range(self.Vpot.nblocks()):
                r_grid = self.Vpot.get_block(r_block)
                r_w = np.array(r_grid.w())
                r_x = np.array(r_grid.x())
                r_y = np.array(r_grid.y())
                r_z = np.array(r_grid.z())
                r_npoints = r_w.shape[0]
    
                points_func.compute_points(r_grid)
    
                r_lpos = np.array(r_grid.functions_local_to_global())
    
                # Compute phi!
                r_phi = np.array(points_func.basis_values()["PHI"])[:r_npoints, :r_lpos.shape[0]]
    
                # Build a local slice of D
                if self.wfn.name() == "CIWavefunction":
                    lD2 = D2[(r_lpos[:, None], r_lpos)]
                    rho2 = contract('pm,mn,pn->p', r_phi, lD2, r_phi)
    
                    p, q, r, s = np.meshgrid(l_lpos, l_lpos, r_lpos, r_lpos, indexing="ij")
                    Tap_temp = Tau_ijkl[p, q, r, s]
    
                    n_xc = contract("mnuv,pm,pn,qu,qv->pq", Tap_temp, l_phi, l_phi, r_phi, r_phi)
                    n_xc *= rho1inv
                    n_xc -= rho2
                elif self.wfn.name() == "RHF":
                    lD2 = self.wfn.Da().np[(l_lpos[:, None], r_lpos)]
                    n_xc = - 2 * contract("mu,nv,pm,pn,qu,qv->pq", lD2, lD2, l_phi, l_phi, r_phi, r_phi)
                    n_xc *= rho1inv

                # Build the distnace matrix
                R2 = (l_x[:, None] - r_x) ** 2
                R2 += (l_y[:, None] - r_y) ** 2
                R2 += (l_z[:, None] - r_z) ** 2
                # R2 += 1e-34
                if np.any(np.isclose(R2, 0.0, atol=atol)):
                    # R2[np.isclose(R2, 0.0)] = np.min(R2[~np.isclose(R2, 0.0)])
                    R2[np.isclose(R2, 0.0, atol=atol)] = np.inf
                Rinv = 1 / np.sqrt(R2)
    
                # if restricted:
                dvp_l += np.sum(n_xc * Rinv * r_w, axis=1)

            # if restricted:
            vxchole[w1_old:w1_old + l_npoints] += dvp_l
            w1_old += l_npoints
    
        print("\n")
        print("Totally %i grid points takes %.2fs with max %i points in a block."
              % (vxchole.shape[0], time.time() - start_time, psi4.core.get_global_option("DFT_BLOCK_MAX_POINTS")))
        assert w1_old == vxchole.shape[0], "Somehow the whole space is not fully integrated."
        if blocks is None:
            # if restricted:
            self.vxc_hole_WF = vxchole
            return self.vxc_hole_WF
        else:
            # if restricted:
            return vxchole

    def _average_local_orbital_energy(self, D, C, eig, grid_info=None):
        """
        (4)(6) in mRKS.
        """

        # Nalpha = self.molecule.nalpha
        # Nbeta = self.molecule.nbeta

        if grid_info is None:
            e_bar = np.zeros(self.npoints_DFT)
            nblocks = self.Vpot.nblocks()

            points_func = self.Vpot.properties()[0]
            points_func.set_deriv(0)
            blocks = None
        else:
            blocks, npoints, points_func = grid_info
            e_bar = np.zeros(npoints)
            nblocks = len(blocks)

            points_func.set_deriv(0)

        # For unrestricted
        iw = 0
        for l_block in range(nblocks):
            # Obtain general grid information
            if blocks is None:
                l_grid = self.Vpot.get_block(l_block)
            else:
                l_grid = blocks[l_block]
            l_npoints = l_grid.npoints()

            points_func.compute_points(l_grid)
            l_lpos = np.array(l_grid.functions_local_to_global())
            l_phi = np.array(points_func.basis_values()["PHI"])[:l_npoints, :l_lpos.shape[0]]
            lD = D[(l_lpos[:, None], l_lpos)]
            lC = C[l_lpos, :]
            rho = contract('pm,mn,pn->p', l_phi, lD, l_phi)
            e_bar[iw:iw + l_npoints] = contract("pm,mi,ni,i,pn->p", l_phi, lC, lC, eig, l_phi) / rho

            iw += l_npoints
        assert iw == e_bar.shape[0], "Somehow the whole space is not fully integrated."
        return e_bar

    def _pauli_kinetic_energy_density(self, D, C, occ=None, Db=None, Cb=None, occb=None, grid_info=None):
        """
        (16)(18) in mRKS. But notice this does not return taup but taup/n
        :return:
        """

        if occ is None:
            occ = np.ones(C.shape[1])

        if grid_info is None:
            taup_rho = np.zeros(self.npoints_DFT)
            nblocks = self.Vpot.nblocks()

            points_func = self.Vpot.properties()[0]
            points_func.set_deriv(1)
            blocks = None

        else:
            blocks, npoints, points_func = grid_info
            taup_rho = np.zeros(npoints)
            nblocks = len(blocks)

            points_func.set_deriv(1)

        iw = 0
        for l_block in range(nblocks):
            # Obtain general grid information
            if blocks is None:
                l_grid = self.Vpot.get_block(l_block)
            else:
                l_grid = blocks[l_block]
            l_npoints = l_grid.npoints()

            points_func.compute_points(l_grid)
            l_lpos = np.array(l_grid.functions_local_to_global())
            l_phi = np.array(points_func.basis_values()["PHI"])[:l_npoints, :l_lpos.shape[0]]
            l_phi_x = np.array(points_func.basis_values()["PHI_X"])[:l_npoints, :l_lpos.shape[0]]
            l_phi_y = np.array(points_func.basis_values()["PHI_Y"])[:l_npoints, :l_lpos.shape[0]]
            l_phi_z = np.array(points_func.basis_values()["PHI_Z"])[:l_npoints, :l_lpos.shape[0]]

            lD = D[(l_lpos[:, None], l_lpos)]

            rho = contract('pm,mn,pn->p', l_phi, lD, l_phi)

            lC = C[l_lpos, :]
            # Matrix Methods
            part_x = contract('pm,mi,nj,pn->ijp', l_phi, lC, lC, l_phi_x)
            part_y = contract('pm,mi,nj,pn->ijp', l_phi, lC, lC, l_phi_y)
            part_z = contract('pm,mi,nj,pn->ijp', l_phi, lC, lC, l_phi_z)
            part1_x = (part_x - np.transpose(part_x, (1, 0, 2))) ** 2
            part1_y = (part_y - np.transpose(part_y, (1, 0, 2))) ** 2
            part1_z = (part_z - np.transpose(part_z, (1, 0, 2))) ** 2


            occ_matrix = np.expand_dims(occ, axis=1) @ np.expand_dims(occ, axis=0)

            taup = np.sum((part1_x + part1_y + part1_z).T * occ_matrix, axis=(1,2)) * 0.5

            taup_rho[iw:iw + l_npoints] = taup / rho ** 2 * 0.5

            iw += l_npoints
        assert iw == taup_rho.shape[0], "Somehow the whole space is not fully integrated."
        return taup_rho

    def _modified_pauli_kinetic_energy_density(self, D, C, occ=None, grid_info=None):
        """
        (16)(18) in mRKS. But notice this does not return taup but taup/n
        :return:
        """

        if occ is None:
            occ = np.ones(C.shape[1])

        if grid_info is None:
            taup_rho = np.zeros(self.npoints_DFT)
            nblocks = self.Vpot.nblocks()

            points_func = self.Vpot.properties()[0]
            points_func.set_deriv(1)
            blocks = None
        else:
            blocks, npoints, points_func = grid_info
            taup_rho = np.zeros(npoints)
            nblocks = len(blocks)

            points_func.set_deriv(1)

        iw = 0
        for l_block in range(nblocks):
            # Obtain general grid information
            if blocks is None:
                l_grid = self.Vpot.get_block(l_block)
            else:
                l_grid = blocks[l_block]
            l_npoints = l_grid.npoints()
            points_func.compute_points(l_grid)
            l_lpos = np.array(l_grid.functions_local_to_global())
            l_phi = np.array(points_func.basis_values()["PHI"])[:l_npoints, :l_lpos.shape[0]]
            l_phi_x = np.array(points_func.basis_values()["PHI_X"])[:l_npoints, :l_lpos.shape[0]]
            l_phi_y = np.array(points_func.basis_values()["PHI_Y"])[:l_npoints, :l_lpos.shape[0]]
            l_phi_z = np.array(points_func.basis_values()["PHI_Z"])[:l_npoints, :l_lpos.shape[0]]
            lD = D[(l_lpos[:, None], l_lpos)]
            rho = contract('pm,mn,pn->p', l_phi, lD, l_phi)
            lC = C[l_lpos, :]
            # Matrix Methods
            part_x = contract('pm,mi,nj,pn->ijp', l_phi_x, lC, lC, l_phi_x)
            part_y = contract('pm,mi,nj,pn->ijp', l_phi_y, lC, lC, l_phi_y)
            part_z = contract('pm,mi,nj,pn->ijp', l_phi_z, lC, lC, l_phi_z)
            phi_iphi_j = contract('pm,mi,nj,pn->ijp', l_phi, lC, lC, l_phi) * (part_x + part_y + part_z)

            occ_matrix = np.expand_dims(occ, axis=1) @ np.expand_dims(occ, axis=0)
            taup = -np.sum(phi_iphi_j.T * occ_matrix, axis=(1, 2))
            taup_rho[iw:iw + l_npoints] = taup / rho ** 2
            iw += l_npoints
        assert iw == taup_rho.shape[0], "Somehow the whole space is not fully integrated."
        return taup_rho

    def mRKS(self, maxiter, vxc_grid=None, v_tol=1e-4, D_tol=1e-7,
             eig_tol=1e-4, frac_old=0.0, init="svwn"):
        """
        the modified Ryabinkin-Kohut-Staroverov method.
        parameters:
        ----------------------
            maxiter: int
                same as opt_max_iter
            vxc_grid: np.ndarray of shape (3, num_grid_points), opt
                When this is given, the final result will be represented
            v_tol: float, opt
                convergence criteria for vxc Fock matrices.
                default: 1e-4
            D_tol: float, opt
                convergence criteria for density matrices.
                default: 1e-7
            eig_tol: float, opt
                convergence criteria for occupied eigenvalue spectrum.
                default: 1e-4
            frac_old: float, opt
                Linear mixing parameter for current vxc and old vxc.
                Should be in [0,1)
                default: 0, i.e. no old vxc is mixed in.
            init: string or psi4.core.Wavefunction, opt
                Initial guess method.
                default: "svwn"
                1) If "continue" is given, then it will not initialize
                but use the densities and orbitals stored. Meaningly,
                one can run a quick WY calculation as the initial
                guess.
                2) If it's not continue, it would be expecting a
                method name string that works for psi4. A separate psi4 calculation
                would be performed.
                3) A user pre-defined psi4.core.Wavefuntion can also be used.


        returns:
        ----------------------
            all are np.ndarray of shape (num_grid_points)
            vxc, vxchole, ebarKS, ebarWF, taup_rho_WF, taup_rho_KS
            in eqn vxc = vxchole + ebarKS - ebarWF + taup_rho_WF - taup_rho_KS.
            Check the original paper for the definition of each component.
    """
        if not self.wfn.name() in ["CIWavefunction", "RHF"]:
            raise ValueError("Currently only supports Psi4 CI wavefunction"
                             "inputs because Psi4 CCSD wavefunction currently "
                             "does not support two-particle density matrices.")

        if self.ref != 1:
            raise ValueError("Currently only supports Spin-Restricted "
                             "calculations since Spin-Unrestricted CI "
                             "is not supported by Psi4.")

        if self.guide_potential_components[0] != "hartree":
            raise ValueError("Hartree potential is necessary as the guide potential.")

        Nalpha = self.nalpha

        # Preparing DFT spherical grid
        functional = psi4.driver.dft.build_superfunctional("SVWN", restricted=True)[0]
        self.Vpot = psi4.core.VBase.build(self.basis, functional, "RV")
        self.Vpot.initialize()
        self.npoints_DFT = 0
        for blk in range(self.Vpot.nblocks()):
            self.npoints_DFT += self.Vpot.get_block(blk).x().shape[0]
        self.Vpot.properties()[0].set_pointers(self.wfn.Da())


        # Preparing for WF properties
        if self.wfn.name() == "CIWavefunction":
            # TPDM & ERI Memory check
            nbf = self.nbf
            I_size = (nbf ** 4) * 8.e-9 * 2
            numpy_memory = 2
            memory_footprint = I_size * 1.5
            if I_size > numpy_memory:
                psi4.core.clean()
                raise Exception("Estimated memory utilization (%4.2f GB) exceeds allotted memory \
                                            limit of %4.2f GB." % (memory_footprint, numpy_memory))
            else:
                print("Memory taken by ERI integral matrix and 2pdm is about: %.3f GB." % memory_footprint)


            opdm = np.array(self.wfn.get_opdm(-1,-1,"SUM",False))
            tpdm = self.wfn.get_tpdm("SUM", True).np

            Ca = self.wfn.Ca().np

            mints = psi4.core.MintsHelper(self.basis)
            I = mints.ao_eri()
            del mints
            # Transfer the AO ERI into MO ERI
            I = contract("ijkl,ip,jq,kr,ls", I, Ca, Ca, Ca, Ca)
            I = 0.5 * I + 0.25 * np.transpose(I, [0, 1, 3, 2]) + 0.25 * np.transpose(I, [1, 0, 2, 3])
            # Transfer the AO h into MO h
            h = Ca.T @ (self.T + self.V) @ Ca

            # Generalized Fock Matrix is constructed on the
            # basis of MOs, which are orthonormal.
            F_GFM = opdm @ h + contract("rsnq,rsmq->mn", I, tpdm)
            F_GFM = 0.5 * (F_GFM + F_GFM.T)

            del I

            C_a_GFM = psi4.core.Matrix(nbf, nbf)
            eigs_a_GFM = psi4.core.Vector(nbf)
            psi4.core.Matrix.from_array(F_GFM).diagonalize(C_a_GFM,
                                                           eigs_a_GFM,
                                                           psi4.core.DiagonalizeOrder.Ascending)

            eigs_a_GFM = eigs_a_GFM.np / 2.0  # RHF
            C_a_GFM = C_a_GFM.np
            # Transfer to AOs
            C_a_GFM = Ca @ C_a_GFM

            # Solving for Natural Orbitals (NO)>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
            C_a_NO = psi4.core.Matrix(nbf, nbf)
            eigs_a_NO = psi4.core.Vector(nbf)
            psi4.core.Matrix.from_array(opdm).diagonalize(C_a_NO, eigs_a_NO, psi4.core.DiagonalizeOrder.Descending)
            eigs_a_NO = eigs_a_NO.np / 2.0  # RHF
            C_a_NO = C_a_NO.np
            C_a_NO = Ca @ C_a_NO

            # prepare properties on the grid
            ebarWF = self._average_local_orbital_energy(self.nt[0], C_a_GFM, eigs_a_GFM)
            taup_rho_WF = self._pauli_kinetic_energy_density(self.nt[0], C_a_NO, eigs_a_NO)
        elif self.wfn.name() == "RHF":
            epsilon_a = self.wfn.epsilon_a_subset("AO", "OCC").np
            ebarWF = self._average_local_orbital_energy(self.nt[0], self.ct[0][:,:Nalpha], epsilon_a)
            taup_rho_WF = self._pauli_kinetic_energy_density(self.nt[0], self.ct[0])
        else:
            raise ValueError("Currently only supports Spin-Restricted "
                             "calculations since Spin-Unrestricted CI "
                             "is not supported by Psi4.")

        vxchole = self._vxc_hole_quadrature()

        emax = np.max(ebarWF)

        # Initialization.
        if type(init) is not str:
            self.Da = np.array(init.Da())
            self.Coca = np.array(init.Ca())[:, :Nalpha]
            self.eigvecs_a = np.array(init.epsilon_a())
        elif init.lower()=="continue":
            pass
        else:
            wfn_temp = psi4.energy(init+"/" + self.basis_str, molecule=self.mol, return_wfn=True)[1]
            self.Da = np.array(wfn_temp.Da())
            self.Coca = np.array(wfn_temp.Ca())[:, :Nalpha]
            self.eigvecs_a = np.array(wfn_temp.epsilon_a())
            del wfn_temp

        vxc_old = 0.0
        Da_old = 0.0
        eig_old = 0.0
        for mRKS_step in range(1, maxiter+1):
            # ebarKS = self._average_local_orbital_energy(self.molecule.Da.np, self.molecule.Ca.np[:,:Nalpha], self.molecule.eig_a.np[:Nalpha] + self.vout_constant)
            ebarKS = self._average_local_orbital_energy(self.Da, self.Coca, self.eigvecs_a[:Nalpha])
            taup_rho_KS = self._pauli_kinetic_energy_density(self.Da, self.Coca)

            # self.vout_constant = emax - self.molecule.eig_a.np[self.molecule.nalpha - 1]
            potential_shift = emax - np.max(ebarKS)
            self.shift = potential_shift

            vxc = vxchole + ebarKS - ebarWF + taup_rho_WF - taup_rho_KS + potential_shift

            # Add compulsory mixing parameter close to the convergence to help convergence HOPEFULLY

            verror = np.linalg.norm(vxc - vxc_old) / self.nbf ** 2
            if verror < v_tol:
                print("vxc stops updating.")
                break
            Derror = np.linalg.norm(self.Da - Da_old) / self.nbf ** 2
            eerror = (np.linalg.norm(self.eigvecs_a[:Nalpha] - eig_old) / Nalpha)
            if (Derror < D_tol) and \
                    (eerror < eig_tol):
                print("KSDFT stops updating.")
                break

            # linear Mixture
            if mRKS_step != 1:
                vxc = vxc * (1 - frac_old) + vxc_old * frac_old

            # Save old data.
            vxc_old = np.copy(vxc)
            Da_old = np.copy(self.Da)
            eig_old = np.copy(self.eigvecs_a[:Nalpha])

            vxc_Fock = self.dft_grid_to_fock(vxc, self.Vpot)

            self._diagonalize_with_potential_mRKS(v=vxc_Fock)

            print("Iter: %i, Density Change: %2.2e, Eigenvalue Change: %2.2e, "
                  "Potential Change: %2.2e." % (mRKS_step,Derror, eerror, verror))

        if vxc_grid is not None:
            grid_info = self.grid_to_blocks(vxc_grid)
            grid_info[-1].set_pointers(self.wfn.Da())
            vxchole = self._vxc_hole_quadrature(grid_info=grid_info)
            if self.wfn.name() == "CIWavefunction":
                ebarWF = self._average_local_orbital_energy(self.nt[0],
                                                            C_a_GFM, eigs_a_GFM, grid_info=grid_info)
                taup_rho_WF = self._pauli_kinetic_energy_density(self.nt[0],
                                                                 C_a_NO, eigs_a_NO, grid_info=grid_info)
            elif self.wfn.name() == "RHF":
                ebarWF = self._average_local_orbital_energy(self.nt[0],
                                                            self.ct[0],
                                                            epsilon_a[:Nalpha],
                                                            grid_info=grid_info)
                taup_rho_WF = self._pauli_kinetic_energy_density(self.nt[0],
                                                                 self.ct[0],
                                                                 grid_info=grid_info)
            ebarKS = self._average_local_orbital_energy(self.Da, self.Coca,
                                                        self.eigvecs_a[:Nalpha], grid_info=grid_info)
            taup_rho_KS = self._pauli_kinetic_energy_density(self.Da, self.Coca,
                                                             grid_info=grid_info)

            potential_shift = np.max(ebarWF) - np.max(ebarKS)
            self.shift = potential_shift

            vxc = vxchole + ebarKS - ebarWF + taup_rho_WF - taup_rho_KS + potential_shift

        return vxc, vxchole, ebarKS, ebarWF, taup_rho_WF, taup_rho_KS

    def _diagonalize_with_potential_mRKS(self, v=None):
        """
        Diagonalize Fock matrix with additional external potential
        """
        if v is None:
            fock_a = self.V + self.T + self.va

        else:
            fock_a = self.V + self.T + self.va + v

        self.Ca, self.Coca, self.Da, self.eigvecs_a = self.diagonalize( fock_a, self.nalpha )

        if self.ref == 1:
            self.Cb, self.Coca, self.Db, self.eigvecs_b = self.Ca.copy(), self.Coca.copy(), self.Da.copy(), self.eigvecs_a.copy()
        else:
            raise ValueError("Currently only spin-restricted in implemented.")