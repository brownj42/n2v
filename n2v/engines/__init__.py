try:
    import pyscf
    has_pyscf = True
except ImportError:
    has_pyscf = False

try:
    import psi4
    has_psi4 = True
except ImportError:
    has_psi4 = False

if has_pyscf:
    from .pyscf import PySCFEngine
if has_psi4:
    from .psi4 import Psi4Engine
